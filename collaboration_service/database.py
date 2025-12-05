from pymongo import MongoClient, ReadPreference, WriteConcern
import redis
import os

# MongoDB connection --> CP configuration
# Replica set for CP (Consistency + Partition Tolerance)
# Write concern "majority" ensures consistency (waits for majority of nodes)
# Read preference SECONDARY_PREFERRED enables eventual consistency reads (high availability)
mongo_uri = os.getenv("MONGO_URI", 
                      "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0")
client = MongoClient(mongo_uri)
db = client.chat_service


# Configure collection for CP writes + eventual reads
# Replica set for CP (Consistency + Partition Tolerance)
# Write concern "majority" ensures consistency (waits for majority of nodes)
# Read preference SECONDARY_PREFERRED enables eventual consistency reads (high availability)
write_concern_majority = WriteConcern(w="majority")

comments_collection = db.get_collection(
    "comments",
    write_concern=write_concern_majority,  # CP: wait for majority before ack --> no duplicates / losses
    read_preference=ReadPreference.SECONDARY_PREFERRED  # Eventual: prefer secondary reads --> high availability
)

# Models collection for caching creator info from events
models_collection = db.get_collection(
    "models",
    write_concern=write_concern_majority
)


def create_indexes():
    """
    Create database indexes on startup for optimal query performance.
    """
    try:
        # Composite index for paginated queries (modelId + createdAt DESC)
        # This is used for sorting comments by creation date
        comments_collection.create_index(
            [("modelId", 1), ("createdAt", -1)],
            background=True,
            name="idx_comments_model_created"
        )
        print("✅ Created index: modelId + createdAt (for paginated queries)")
        
        # Index for parentId lookups (for building comment trees)
        comments_collection.create_index(
            [("parentId", 1)],
            background=True,
            name="idx_comments_parent"
        )
        print("✅ Created index: parentId (for tree building)")
        
        # Index for modelId + parentId queries (for fetching replies and count queries)
        # This composite index is optimal for both reply lookups and counting top-level comments
        comments_collection.create_index(
            [("modelId", 1), ("parentId", 1)],
            background=True,
            name="idx_comments_model_parent"
        )
        print("✅ Created index: modelId + parentId (for reply queries and count queries)")
        
        # Index for models collection (for creator lookups)
        models_collection.create_index(
            [("modelId", 1)],
            background=True,
            name="idx_models_model_id"
        )
        print("✅ Created index: modelId (for creator lookups)")
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"⚠️  Could not create all indexes: {e}")
        # Continue - indexes might already exist



# Redis for caching with Sentinel support
# Use Sentinel if configured, otherwise fallback to direct connection
REDIS_HOST = os.getenv("REDIS_HOST", "redis-master")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER_NAME", "mymaster")

def get_redis_client():
    """Get Redis client with Sentinel support (for writes to master)"""
    try:
        # Try Sentinel first if configured
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
            
            # Get master connection (for writes, Sentinel handles failover)
            client = sentinel.master_for(
                REDIS_SENTINEL_MASTER_NAME,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                db=0,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0
            )
            
            # Test connection
            client.ping()
            return client
        else:
            # Fallback to direct connection
            client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                retry_on_timeout=False
            )
            # Test connection
            client.ping()
            return client
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Redis connection failed: {e}, cache will be unavailable")
        return None

# Initialize Redis client
cache = get_redis_client()