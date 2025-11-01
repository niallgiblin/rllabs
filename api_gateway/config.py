from pydantic_settings import BaseSettings
from typing import Dict, List

class Settings(BaseSettings):
    GATEWAY_URL: str = "http://localhost:8080"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    MONITORING_ENABLED: bool = True
    
    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    
    # Service Registry
    SERVICES: Dict[str, str] = {
        "/api/models": "http://model_catalog_service:8000",
        # "/api/upload": "http://upload-download-service:8001",
        # "/api/download": "http://upload-download-service:8001",
        # "/api/training": "http://training-service:8002",
        # "/api/mazes": "http://maze-service:8003",
        # "/api/comments": "http://comment-service:8004",
    }
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60  # seconds
    
    class Config:
        env_file = ".env"

settings = Settings()