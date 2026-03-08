"""Base class for Cosmos DB-backed stores with in-memory fallback.

Eliminates duplicated ``__aenter__``/``__aexit__`` boilerplate and
query accumulation loops across all Cosmos stores.

Each subclass must set ``_container_name`` and keeps its own
``_memory: ClassVar[dict[str, dict]]`` for in-memory fallback state.
"""

from typing import Any, ClassVar, Protocol, Self, TypeVar

from sjifire.core.config import get_cosmos_container


class _FromCosmos(Protocol):
    """Protocol for models that can be constructed from Cosmos DB items."""

    @classmethod
    def from_cosmos(cls, data: dict) -> Self: ...


T = TypeVar("T", bound=_FromCosmos)


class CosmosStore:
    """Base class for Cosmos DB-backed stores with in-memory fallback.

    Subclasses must define ``_container_name`` (the Cosmos container to
    connect to) and their own ``_memory`` class variable for fallback
    storage.

    Usage::

        class MyStore(CosmosStore):
            _container_name: ClassVar[str] = "my-container"
            _memory: ClassVar[dict[str, dict]] = {}

            async def get(self, doc_id: str) -> MyModel | None:
                if self._in_memory:
                    ...
                return await self._query_one(
                    "SELECT * FROM c WHERE c.id = @id",
                    [{"name": "@id", "value": doc_id}],
                    MyModel,
                )
    """

    _container_name: ClassVar[str]

    def __init__(self) -> None:
        """Initialize store. Call ``__aenter__`` to connect."""
        self._container = None
        self._in_memory = False

    async def __aenter__(self) -> Self:
        """Get a container client from the shared Cosmos connection pool."""
        self._container = await get_cosmos_container(self._container_name)
        if self._container is None:
            self._in_memory = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        """No-op -- shared Cosmos client stays alive."""
        self._container = None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def _query_one(
        self,
        query: str,
        parameters: list[dict],
        model_class: type[T],
        **kwargs: Any,
    ) -> T | None:
        """Run a query expecting 0-1 results.

        Extra keyword arguments (e.g. ``partition_key``) are forwarded
        to ``query_items``.

        Args:
            query: Cosmos SQL query string
            parameters: Query parameter list
            model_class: Model class with a ``from_cosmos`` classmethod
            **kwargs: Additional keyword arguments for ``query_items``

        Returns:
            A single model instance, or None if no results
        """
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            max_item_count=1,
            **kwargs,
        ):
            return model_class.from_cosmos(item)
        return None

    async def _query_many(
        self,
        query: str,
        parameters: list[dict] | None,
        model_class: type[T],
        *,
        max_items: int = 100,
        **kwargs: Any,
    ) -> list[T]:
        """Run a query accumulating up to *max_items* results.

        Extra keyword arguments (e.g. ``max_item_count``) are forwarded
        to ``query_items``.

        Args:
            query: Cosmos SQL query string
            parameters: Query parameter list (may be None)
            model_class: Model class with a ``from_cosmos`` classmethod
            max_items: Stop accumulating after this many results
            **kwargs: Additional keyword arguments for ``query_items``

        Returns:
            List of model instances (at most *max_items*)
        """
        items: list[T] = []
        async for item in self._container.query_items(
            query=query,
            parameters=parameters,
            **kwargs,
        ):
            items.append(model_class.from_cosmos(item))
            if len(items) >= max_items:
                break
        return items
