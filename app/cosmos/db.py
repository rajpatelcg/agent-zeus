import datetime
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError
from app.config.src import COSMOS_ENDPOINT, COSMOS_KEY, DATABASE_NAME, CONTAINER_NAME

async def save_final_payload_to_cosmos(payload: dict):
    """
    Saves the strictly formatted final payload to Azure Cosmos DB.
    Enforces Cosmos-required fields (id and partition key).
    """
    if not COSMOS_ENDPOINT or not COSMOS_KEY:
        print("[Cosmos] Skipping save: Endpoint or Key missing in configuration.")
        return

    try:
        async with CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY) as client:
            db = client.get_database_client(DATABASE_NAME)
            container = db.get_container_client(CONTAINER_NAME)
            
            document = payload.copy()
            
            # Enforce Cosmos DB system fields
            document["id"] = payload.get("case_id") or payload.get("call_id")
            document["user_id"] = payload.get("customer_number")
            
            # Add a storage timestamp
            document["stored_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            
            await container.upsert_item(document)
            print(f"[Cosmos] Successfully stored clean result for {document['id']}")
            
    except CosmosHttpResponseError as e:
        print(f"[Cosmos] Failed to save result. Error: {e.message}")
    except Exception as e:
        print(f"[Cosmos] Unexpected Error: {e}")


# async def save_to_humanevaluation(payload: dict):
#     """
#     Saves the strictly formatted final payload to Azure Cosmos DB.
#     Enforces Cosmos-required fields (id and partition key).
#     """
#     if not COSMOS_ENDPOINT or not COSMOS_KEY:
#         print("[Cosmos] Skipping save: Endpoint or Key missing in configuration.")
#         return

#     try:
#         async with CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY) as client:
#             db = client.get_database_client(DATABASE_NAME)
#             container = db.get_container_client(CONTAINER_NAME)
            
#             document = payload.copy()
            
#             # Enforce Cosmos DB system fields
#             document["id"] = payload.get("case_id") or payload.get("call_id")
#             document["user_id"] = payload.get("customer_number")
            
#             # Add a storage timestamp
#             document["stored_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            
#             await container.upsert_item(document)
#             print(f"[Cosmos] Successfully stored clean result for {document['id']}")
            
#     except CosmosHttpResponseError as e:
#         print(f"[Cosmos] Failed to save result. Error: {e.message}")
#     except Exception as e:
#         print(f"[Cosmos] Unexpected Error: {e}")


# DEFAULT_CONFIDENCE_THRESHOLD=0.8


# async def fetch_low_confidence_records_from_cosmos(
#     threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
#     limit: Optional[int] = None
# ) -> List[Dict[str, Any]]:
#     """
#     Fetch records from Cosmos DB where confidence_score
#     is less than the given threshold.
#     """

#     if not COSMOS_ENDPOINT or not COSMOS_KEY:
#         print("[Cosmos] Missing ENDPOINT or KEY in environment.")
#         return []

#     if not DATABASE_NAME or not CONTAINER_NAME:
#         print("[Cosmos] Missing DATABASE_NAME or CONTAINER_NAME in environment.")
#         return []

#     records = []

#     try:
#         async with CosmosClient(
#             COSMOS_ENDPOINT,
#             credential=COSMOS_KEY
#         ) as client:

#             database = client.get_database_client(DATABASE_NAME)
#             container = database.get_container_client(CONTAINER_NAME)

#             query = """
#             SELECT *
#             FROM c
#             WHERE IS_DEFINED(c.confidence_score)
#             AND c.confidence_score < @threshold
#             """

#             parameters = [
#                 {
#                     "name": "@threshold",
#                     "value": threshold
#                 }
#             ]

#             results = container.query_items(
#                 query=query,
#                 parameters=parameters,
#                 # enable_cross_partition_query=True
#             )

#             async for item in results:
#                 records.append(item)

#                 if limit is not None and len(records) >= limit:
#                     break

#             print(
#                 f"[Cosmos] Found {len(records)} records "
#                 f"where confidence_score < {threshold}"
#             )

#             return records

#     except CosmosHttpResponseError as e:
#         print(f"[Cosmos] Query failed: {e.message}")
#         return []

#     except Exception as e:
#         print(f"[Cosmos] Unexpected error: {e}")
#         return []


# async def approved_records(
#     review
# ) :
    

#     if not COSMOS_ENDPOINT or not COSMOS_KEY:
#         print("[Cosmos] Missing ENDPOINT or KEY in environment.")
#         return []

#     if not DATABASE_NAME or not CONTAINER_NAME:
#         print("[Cosmos] Missing DATABASE_NAME or CONTAINER_NAME in environment.")
#         return []

#     records = []

#     try:
#         async with CosmosClient(
#             COSMOS_ENDPOINT,
#             credential=COSMOS_KEY
#         ) as client:

#             database = client.get_database_client(DATABASE_NAME)
#             container = database.get_container_client(CONTAINER_NAME)

#             item=conatiner.read_item(
#                 item=review["id"]

#             )
#             item["approved"]=review["approved"]
#             item["reviewed"]=review["reviewed"]
            
            
#             return item