from pydantic_settings import BaseSettings
from typing import Dict, List

class Settings(BaseSettings):
    GATEWAY_URL: str = "http://localhost:8080"
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000", 
        "http://localhost:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ]
    MONITORING_ENABLED: bool = True
    
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    
    REDIS_SENTINEL_HOSTS: str = ""
    REDIS_SENTINEL_MASTER_NAME: str = "mymaster"
    REDIS_PASSWORD: str = ""
    
    SERVICES: Dict[str, str] = {
        
        "/api/uploads": "http://upload-download-service:8002",
        "/api/downloads": "http://upload-download-service:8002",
        "/api/artifacts": "http://upload-download-service:8002",
        
        "/api/comments": "http://collaboration-service:8000",
        
        "/api/models": "http://model-catalog-service:8000",
        "/api/versions": "http://model-catalog-service:8000",
        
        "/api/training-jobs": "http://upload-download-service:8002",
    }
    
    RATE_LIMIT_REQUESTS: int = 1000 
    RATE_LIMIT_WINDOW: int = 60  
    
    class Config:
        env_file = ".env"

settings = Settings()