# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
from typing import Any
import uuid
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types
import httpx

from .a2ui_utils import a2ui_callback

load_dotenv()

# Hardcode project ID, Cloud Storage bucket name, and Memory Bank ID
PROJECT_ID = "qwiklabs-gcp-03-d9b66f1516c2"
BUCKET_NAME = "travel-concierge-images-qwiklabs-gcp-03-d9b66f1516c2"
MEMORY_BANK_ID = "8918382904472502272"

# Ensure Vertex AI environment variables are set for Gemini model
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-east1"

_db = None


def memory_service_builder() -> VertexAiMemoryBankService:
    """Builder function to construct the VertexAiMemoryBankService for deployed runtime."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


memory_service = memory_service_builder()


async def generate_memories_callback(callback_context: CallbackContext) -> None:
    """Callback triggered after each turn to extract and persist durable memories to Vertex AI Memory Bank."""
    await callback_context.add_session_to_memory()
    return None


def get_firestore_client() -> firestore.Client:
    """Helper to lazy-initialize the Firestore client with the hardcoded project ID."""
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


def list_destinations() -> list[dict[str, Any]]:
    """List all available travel destinations stored in the Firestore database.

    Returns:
        A list of destination dictionaries containing details like name, country, description, and budget.
    """
    db = get_firestore_client()
    docs = db.collection("destinations").stream()
    destinations = []
    for doc in docs:
        data = doc.to_dict()
        data["destination_id"] = doc.id
        destinations.append(data)
    return destinations


def get_destination_details(destination_id: str) -> dict[str, Any]:
    """Retrieve detailed information about a specific travel destination from Firestore by its destination ID.

    Args:
        destination_id: The unique ID of the destination (e.g. 'tokyo', 'kyoto', 'paris', 'bali').

    Returns:
        A dictionary with the destination details, or an error message if not found.
    """
    db = get_firestore_client()
    doc_ref = db.collection("destinations").document(destination_id.lower().strip())
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        data["destination_id"] = doc.id
        return data
    return {"error": f"Destination with ID '{destination_id}' was not found in Firestore."}


def add_destination(
    destination_id: str,
    name: str,
    country: str,
    description: str,
    best_season: str,
    budget_category: str,
    average_cost_per_day_usd: int,
    highlights: list[str],
) -> str:
    """Add a new travel destination or update an existing destination in the Firestore database.

    Args:
        destination_id: Unique string key for the destination (e.g., 'sydney').
        name: Full display name (e.g., 'Sydney, Australia').
        country: Country name (e.g., 'Australia').
        description: Brief overview of the destination.
        best_season: Ideal travel season (e.g., 'Spring / Autumn').
        budget_category: Budget tier like 'Budget', 'Moderate', or 'High'.
        average_cost_per_day_usd: Estimated daily cost in USD.
        highlights: Key attractions or highlights as a list of strings.

    Returns:
        A confirmation message indicating the destination was saved to Firestore.
    """
    db = get_firestore_client()
    doc_id = destination_id.lower().strip()
    dest_data = {
        "destination_id": doc_id,
        "name": name,
        "country": country,
        "description": description,
        "best_season": best_season,
        "budget_category": budget_category,
        "average_cost_per_day_usd": average_cost_per_day_usd,
        "highlights": highlights,
    }
    db.collection("destinations").document(doc_id).set(dest_data)
    return f"Destination '{name}' (ID: {doc_id}) successfully saved to Firestore."


def generate_destination_image(prompt: str, tool_context: ToolContext) -> str:
    """Generate a scenic AI image preview for a travel destination or landmark using gemini-3.1-flash-lite-image model.

    Saves the image artifact to the ADK Playground and uploads it to public Cloud Storage.

    Args:
        prompt: Detailed visual description of the destination image to generate (e.g., 'A sunset view of Kyoto bamboo forest').
        tool_context: ADK ToolContext used to save artifacts to the Playground.

    Returns:
        The public HTTPS URL of the generated image hosted in Cloud Storage.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if not image_bytes:
            return "Error: Image generation model did not return image data."

        filename = f"destination_{uuid.uuid4().hex[:8]}.jpg"

        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading destination image: {str(e)}"


def generate_destination_video(prompt: str, tool_context: ToolContext) -> str:
    """Generate a short cinematic video preview for a travel destination or landmark using Google's gemini-omni-flash-preview model in global region.

    Saves the video artifact to the ADK Playground and uploads it to public Cloud Storage.

    Args:
        prompt: Detailed visual description of the destination video to generate (e.g., 'A 5-second cinematic aerial shot of tropical Bali beaches at sunset').
        tool_context: ADK ToolContext used to save artifacts to the Playground.

    Returns:
        The public HTTPS URL of the generated video hosted in Cloud Storage.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        video_bytes = None
        mime_type = "video/mp4"

        try:
            response = genai_client.models.generate_content(
                model="gemini-omni-flash-preview",
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["VIDEO"]),
            )
            if hasattr(response, "candidates") and response.candidates:
                for candidate in response.candidates:
                    if hasattr(candidate, "content") and candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if getattr(part, "inline_data", None) and part.inline_data.data:
                                video_bytes = part.inline_data.data
                                mime_type = part.inline_data.mime_type or "video/mp4"
                                break
        except Exception:
            pass

        if not video_bytes:
            import base64
            interaction = genai_client.interactions.create(
                model="gemini-omni-flash-preview",
                input=prompt,
            )
            if hasattr(interaction, "output_video") and interaction.output_video and getattr(interaction.output_video, "data", None):
                raw_data = interaction.output_video.data
                if isinstance(raw_data, str):
                    video_bytes = base64.b64decode(raw_data)
                else:
                    video_bytes = raw_data
                mime_type = getattr(interaction.output_video, "mime_type", "video/mp4") or "video/mp4"

        if not video_bytes:
            return "Error: Video generation model gemini-omni-flash-preview did not return video data."

        filename = f"destination_video_{uuid.uuid4().hex[:8]}.mp4"

        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading destination video: {str(e)}"


def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert a monetary amount from one currency to another using live exchange rates.

    Args:
        amount: The numeric money amount to convert (e.g., 100.0 or 150.0).
        from_currency: 3-letter source currency code (e.g., 'USD', 'EUR', 'GBP').
        to_currency: 3-letter target currency code (e.g., 'JPY', 'EUR', 'IDR').

    Returns:
        A string describing the converted amount and current exchange rate.
    """
    base = from_currency.upper().strip()
    target = to_currency.upper().strip()
    try:
        response = httpx.get(f"https://open.er-api.com/v6/latest/{base}", timeout=10.0)
        response.raise_for_status()
        data = response.json()
        rates = data.get("rates", {})
        if target not in rates:
            return f"Error: Target currency code '{target}' was not found in exchange rate data."
        rate = rates[target]
        converted_amount = amount * rate
        return (
            f"{amount} {base} = {converted_amount:,.2f} {target} "
            f"(Current exchange rate: 1 {base} = {rate} {target})"
        )
    except Exception as e:
        return f"Error fetching exchange rate: {str(e)}"


def get_live_weather(location: str) -> str:
    """Fetch live real-time weather and temperature for any destination worldwide using the Open-Meteo public API.

    Args:
        location: Name of the city or location (e.g., 'Tokyo', 'Paris', 'Bali', 'Kyoto').

    Returns:
        A string describing the location's current weather conditions, temperature, and wind speed.
    """
    api_key = os.environ.get("OPEN_METEO_API_KEY", "")
    try:
        geo_resp = httpx.get(
            f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1",
            timeout=10.0,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        results = geo_data.get("results")
        if not results:
            return f"Location '{location}' not found in geocoding database."

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        place_name = place.get("name", location)
        country = place.get("country", "")

        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        if api_key:
            weather_url += f"&apikey={api_key}"

        weather_resp = httpx.get(weather_url, timeout=10.0)
        weather_resp.raise_for_status()
        cw = weather_resp.json().get("current_weather", {})
        temp_c = cw.get("temperature", 0.0)
        temp_f = (temp_c * 9 / 5) + 32
        wind_speed = cw.get("windspeed", 0.0)

        full_location = f"{place_name}, {country}".strip(", ")
        return (
            f"Current weather in {full_location}: {temp_c:.1f}°C ({temp_f:.1f}°F), "
            f"wind speed {wind_speed} km/h."
        )
    except Exception as e:
        return f"Error fetching live weather for '{location}': {str(e)}"


def geocode_address(address: str) -> dict[str, Any]:
    """Geocode a physical address or place name into geographic coordinates (latitude and longitude) using the Google Maps Geocoding API.

    Args:
        address: The location, address, or landmark to geocode (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA' or 'Eiffel Tower, Paris').

    Returns:
        A dictionary containing formatted_address, location coordinates (lat, lng), and place_id.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return {"error": "GOOGLE_MAPS_API_KEY is not configured in .env file."}

    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": api_key}
        resp = httpx.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return {"error": f"No geocoding results found for address: '{address}'."}

        top = results[0]
        return {
            "formatted_address": top.get("formatted_address"),
            "location": top.get("geometry", {}).get("location", {}),
            "place_id": top.get("place_id"),
        }
    except Exception as e:
        return {"error": f"Geocoding request failed: {str(e)}"}


def search_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "tourist_attraction",
    radius_meters: float = 2000.0,
) -> list[dict[str, Any]]:
    """Find nearby places (restaurants, tourist attractions, hotels, etc.) around coordinates using Google Places API (New).

    Args:
        latitude: Center latitude coordinate.
        longitude: Center longitude coordinate.
        place_type: Type of place to search for (e.g., 'restaurant', 'tourist_attraction', 'lodging', 'museum').
        radius_meters: Search radius around center coordinates in meters (default: 2000).

    Returns:
        A list of place dictionaries containing name, formatted_address, and location coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return [{"error": "GOOGLE_MAPS_API_KEY is not configured in .env file."}]

    try:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        }
        body = {
            "includedTypes": [place_type],
            "maxResultCount": 10,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(radius_meters),
                }
            },
        }
        resp = httpx.post(url, headers=headers, json=body, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        places = data.get("places", [])
        results = []
        for p in places:
            display_name = p.get("displayName", {}).get("text", "")
            results.append({
                "name": display_name,
                "formatted_address": p.get("formattedAddress"),
                "location": p.get("location"),
            })
        return results
    except Exception as e:
        return [{"error": f"Places Nearby Search failed: {str(e)}"}]


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


# Read Agent Engine resource name from deployment_metadata.json if available
agent_engine_resource_name = None
metadata_file_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(metadata_file_path):
    try:
        with open(metadata_file_path, "r") as f:
            metadata = json.load(f)
            agent_engine_resource_name = metadata.get("remote_agent_runtime_id")
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=agent_engine_resource_name
)

# Build A2UI 0.8 system prompt using A2uiSchemaManager and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description="Travel Concierge Agent, an AI travel assistant that helps users discover and plan trips.",
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects. "
        "You MUST remember and track all user stated allergies, dietary restrictions (e.g. gluten, "
        "nuts, dairy, shellfish, penicillin, etc.), health preferences, and travel facts across conversations. "
        "Always inspect preloaded user memories at the start of each session for allergy information and ensure "
        "that all dining, hotel, activity, and itinerary recommendations explicitly account for and accommodate "
        "the user's allergies."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        list_destinations,
        get_destination_details,
        add_destination,
        generate_destination_image,
        generate_destination_video,
        convert_currency,
        get_live_weather,
        geocode_address,
        search_nearby_places,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
