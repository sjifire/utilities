import logging

# Azure SDK emits HTTP-level logs at INFO — silence globally so all
# Cosmos DB stores (dispatch, incidents, schedule, tokens, NERIS) are quiet.
logging.getLogger("azure").setLevel(logging.WARNING)
