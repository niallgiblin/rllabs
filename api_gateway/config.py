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
    # Routes are matched in order. most specific routes should come first
    SERVICES: Dict[str, str] = {
        
        # Upload/Download Service
        "/api/uploads": "http://upload-download-service:8002",
        "/api/downloads": "http://upload-download-service:8002",
        
        # Model Catalog Service
        "/api/models": "http://model-catalog-service:8000",
        
        # Future services (commented out until implemented)
        # "/api/training": "http://training-service:8003",
        # "/api/mazes": "http://maze-service:8004",
        # "/api/comments": "http://comment-service:8005",
    }
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60  # seconds
    
    class Config:
        env_file = ".env"

settings = Settings()