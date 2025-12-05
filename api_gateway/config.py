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
    
    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    
    # Redis Sentinel (optional, for HA)
    # Format: "host1:port1,host2:port2,host3:port3"
    REDIS_SENTINEL_HOSTS: str = ""
    REDIS_SENTINEL_MASTER_NAME: str = "mymaster"
    REDIS_PASSWORD: str = ""
    
    # Service Registry
    # Routes are matched in order. most specific routes should come first
    SERVICES: Dict[str, str] = {
        
        # Upload/Download Service
        "/api/uploads": "http://upload-download-service:8002",
        "/api/downloads": "http://upload-download-service:8002",
        
        # Collaboration Service (comments)
        "/api/comments": "http://collaboration-service:8000",
        
        # Model Catalog Service
        "/api/models": "http://model-catalog-service:8000",
        "/api/versions": "http://model-catalog-service:8000",
        
        # Training Jobs (handled by Upload/Download Service)
        "/api/training-jobs": "http://upload-download-service:8002",
    }
    
    # Rate Limiting
    # Increased limits for load testing: 1000 req/min per user/IP
    # For production, adjust based on expected traffic patterns
    RATE_LIMIT_REQUESTS: int = 1000  # Increased from 100 for load testing
    RATE_LIMIT_WINDOW: int = 60  # seconds
    
    class Config:
        env_file = ".env"

settings = Settings()