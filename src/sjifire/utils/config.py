"""Configuration management using Pydantic settings."""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ESO Suite credentials
    eso_username: str = Field(default="", description="ESO login username")
    eso_password: str = Field(default="", description="ESO login password")
    eso_agency: str = Field(default="", description="ESO agency/organization code")

    # Microsoft Graph API
    ms_graph_tenant_id: str = Field(default="", description="Azure AD tenant ID")
    ms_graph_client_id: str = Field(default="", description="App registration client ID")
    ms_graph_client_secret: str = Field(default="", description="App registration client secret")

    # Power Automate
    power_automate_url: str = Field(default="", description="HTTP trigger URL for Power Automate")

    # Microsoft Form
    ms_form_id: str = Field(default="", description="Form ID from Microsoft Forms")
    ms_form_apparatus_question_id: str = Field(default="", description="Question ID for apparatus")
    ms_form_personnel_question_id: str = Field(default="", description="Question ID for personnel")

    # Paths
    config_dir: Path = Field(default=Path("config"), description="Config files directory")
    screenshots_dir: Path = Field(default=Path("screenshots"), description="Screenshots directory")

    @property
    def has_eso_credentials(self) -> bool:
        """Check if ESO credentials are configured."""
        return bool(self.eso_username and self.eso_password and self.eso_agency)

    @property
    def has_graph_credentials(self) -> bool:
        """Check if MS Graph credentials are configured."""
        return bool(
            self.ms_graph_tenant_id and self.ms_graph_client_id and self.ms_graph_client_secret
        )

    @property
    def has_power_automate(self) -> bool:
        """Check if Power Automate URL is configured."""
        return bool(self.power_automate_url)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
