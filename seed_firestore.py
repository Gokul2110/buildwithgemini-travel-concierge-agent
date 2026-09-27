import logging
from google.cloud import firestore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Hardcoded project ID as required
PROJECT_ID = "qwiklabs-gcp-03-d9b66f1516c2"

SEED_DESTINATIONS = [
    {
        "destination_id": "tokyo",
        "name": "Tokyo, Japan",
        "country": "Japan",
        "description": "A bustling metropolis combining ultra-modern technology with traditional historic temples.",
        "best_season": "Spring / Autumn",
        "budget_category": "Moderate",
        "average_cost_per_day_usd": 180,
        "highlights": ["Shinjuku Gyoen", "Shibuya Crossing", "Senso-ji Temple", "Akihabara"],
    },
    {
        "destination_id": "kyoto",
        "name": "Kyoto, Japan",
        "country": "Japan",
        "description": "Japan's cultural heart filled with classical Buddhist temples, gardens, imperial palaces, and wooden houses.",
        "best_season": "Spring / Autumn",
        "budget_category": "Moderate",
        "average_cost_per_day_usd": 150,
        "highlights": ["Fushimi Inari Shrine", "Arashiyama Bamboo Grove", "Kinkaku-ji (Golden Pavilion)"],
    },
    {
        "destination_id": "paris",
        "name": "Paris, France",
        "country": "France",
        "description": "The City of Light, famous for its world-class art, gastronomy, fashion, and iconic architecture.",
        "best_season": "Spring / Summer",
        "budget_category": "High",
        "average_cost_per_day_usd": 220,
        "highlights": ["Eiffel Tower", "Louvre Museum", "Notre-Dame Cathedral", "Montmartre"],
    },
    {
        "destination_id": "bali",
        "name": "Bali, Indonesia",
        "country": "Indonesia",
        "description": "An Indonesian island known for its forested volcanic mountains, iconic rice paddies, beaches and coral reefs.",
        "best_season": "Dry Season (May to September)",
        "budget_category": "Budget",
        "average_cost_per_day_usd": 80,
        "highlights": ["Ubud Monkey Forest", "Tegallalang Rice Terraces", "Uluwatu Temple", "Seminyak Beach"],
    },
]


def seed_database():
    logger.info("Connecting to Firestore with project ID: %s", PROJECT_ID)
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection("destinations")

    for dest in SEED_DESTINATIONS:
        doc_id = dest["destination_id"]
        collection_ref.document(doc_id).set(dest)
        logger.info("Seeded destination doc: %s", doc_id)

    logger.info("Successfully seeded %d destinations into Firestore!", len(SEED_DESTINATIONS))


if __name__ == "__main__":
    seed_database()
