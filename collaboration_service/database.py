from pymongo import MongoClient, ReadPreference, WriteConcern
import redis
import os

mongo_uri = os.getenv("MONGO_URI", 
                      "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0")
client = MongoClient(mongo_uri)
db = client.chat_service

write_concern_majority = WriteConcern(w="majority")

comments_collection = db.get_collection(
    "comments",
    write_concern=write_concern_majority,  
    read_preference=ReadPreference.SECONDARY_PREFERRED  
)

models_collection = db.get_collection(
    "models",
    write_concern=write_concern_majority
)


def create_indexes():
    """
    Create database indexes on startup for optimal query performance.
    """
    try:
        comments_collection.create_index(
            [("modelId", 1), ("createdAt", -1)],
            background=True,
            name="idx_comments_model_created"
        )
        print("Created index: modelId + createdAt (for paginated queries)")
        
        comments_collection.create_index(
            [("parentId", 1)],
            background=True,
            name="idx_comments_parent"
        )
        print("Created index: parentId (for tree building)")
        
        comments_collection.create_index(
            [("modelId", 1), ("parentId", 1)],
            background=True,
            name="idx_comments_model_parent"
        )
        print("Created index: modelId + parentId (for reply queries and count queries)")
        
        models_collection.create_index(
            [("modelId", 1)],
            background=True,
            name="idx_models_model_id"
        )
        print("✅ Created index: modelId (for creator lookups)")
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Could not create all indexes: {e}")


REDIS_HOST = os.getenv("REDIS_HOST", "redis-master")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER_NAME", "mymaster")

def get_redis_client():
    """Get Redis client with Sentinel support (for writes to master)"""
    try:
        if REDIS_SENTINEL_HOSTS and REDIS_SENTINEL_MASTER_NAME:
            from redis.sentinel import Sentinel
            
            sentinel_hosts = []
            for host_port in REDIS_SENTINEL_HOSTS.split(","):
                host_port = host_port.strip()
                if ":" in host_port:
                    host, port = host_port.split(":")
                    sentinel_hosts.append((host, int(port)))
                else:
                    sentinel_hosts.append((host_port, 26379))
            
            sentinel = Sentinel(
                sentinel_hosts,
                socket_timeout=1.0,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None
            )
            
            client = sentinel.master_for(
                REDIS_SENTINEL_MASTER_NAME,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                db=0,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0
            )
            
            client.ping()
            return client
        else:
            client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                retry_on_timeout=False
            )
            client.ping()
            return client
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Redis connection failed: {e}, cache will be unavailable")
        return None

cache = get_redis_client()