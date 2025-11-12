import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import (
    Ingredient,
    Recipe,
    InventoryItem,
    Order,
    ReportFilter,
    BatchLot,
    Customer,
    Tutorial,
    Notification,
)

app = FastAPI(title="Gelato Pro Suite API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Gelato Pro Suite API running"}

@app.get("/test")
def test_database():
    response = {"backend": "✅ Running", "database": "❌ Not Available"}
    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["collections"] = db.list_collection_names()
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response

# Utilities

def to_object_id(id_str: str):
    try:
        return ObjectId(id_str)
    except Exception:
        return id_str  # allow plain strings if inserted as such

# Calculations

def compute_recipe_metrics(recipe: Recipe) -> Dict[str, float]:
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    total_grams = 0.0
    totals = {
        "solids_g": 0.0,
        "fat_g": 0.0,
        "sugar_g": 0.0,
        "lactose_g": 0.0,
        "stabilizer_g": 0.0,
        "sweetness_equiv": 0.0,
        "cost": 0.0,
    }

    for comp in recipe.components:
        ing = db["ingredient"].find_one({"_id": to_object_id(comp.ingredient_id)})
        if not ing:
            raise HTTPException(status_code=404, detail=f"Ingredient not found: {comp.ingredient_id}")
        g = comp.grams
        total_grams += g
        totals["solids_g"] += g * (ing.get("total_solids_pct", 0) / 100)
        totals["fat_g"] += g * (ing.get("fat_pct", 0) / 100)
        sugar_pct = ing.get("sugar_pct", 0)
        lactose_pct = ing.get("lactose_pct", 0)
        totals["sugar_g"] += g * (sugar_pct / 100)
        totals["lactose_g"] += g * (lactose_pct / 100)
        totals["stabilizer_g"] += g * (ing.get("stabilizer_pct", 0) / 100)
        totals["sweetness_equiv"] += g * (sugar_pct / 100) * ing.get("sweetness_equiv", 1.0)
        cost_per_kg = ing.get("cost_per_kg", 0.0)
        totals["cost"] += (g / 1000.0) * cost_per_kg

    if total_grams <= 0:
        raise HTTPException(status_code=400, detail="Recipe has no components or zero quantity")

    per_kg_factor = 1000.0 / total_grams

    return {
        "total_weight_g": round(total_grams, 2),
        "solids_pct": round((totals["solids_g"] * per_kg_factor) / 10, 2),
        "fat_pct": round((totals["fat_g"] * per_kg_factor) / 10, 2),
        "sugars_pct": round((totals["sugar_g"] * per_kg_factor) / 10, 2),
        "lactose_pct": round((totals["lactose_g"] * per_kg_factor) / 10, 2),
        "stabilizers_pct": round((totals["stabilizer_g"] * per_kg_factor) / 10, 2),
        "sweetness_equiv": round(totals["sweetness_equiv"] * per_kg_factor / 10, 2),
        "food_cost_per_kg": round(totals["cost"] * per_kg_factor, 2),
    }

# Ingredients
class CreateIngredientRequest(Ingredient):
    pass

@app.post("/api/ingredients")
def create_ingredient(payload: CreateIngredientRequest):
    _id = create_document("ingredient", payload)
    return {"id": _id}

@app.get("/api/ingredients")
def list_ingredients(limit: Optional[int] = 200):
    docs = get_documents("ingredient", {}, limit or 200)
    for d in docs:
        if "_id" in d:
            d["id"] = str(d.pop("_id"))
    return docs

# Recipes
class CreateRecipeRequest(Recipe):
    pass

@app.post("/api/recipes")
def create_recipe(payload: CreateRecipeRequest):
    _id = create_document("recipe", payload)
    return {"id": _id}

@app.get("/api/recipes")
def list_recipes(limit: Optional[int] = 200):
    docs = get_documents("recipe", {}, limit or 200)
    for d in docs:
        if "_id" in d:
            d["id"] = str(d.pop("_id"))
    return docs

class ComputeMetricsRequest(BaseModel):
    recipe_id: str

@app.post("/api/recipes/compute")
def compute_recipe(payload: ComputeMetricsRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    rec = db["recipe"].find_one({"_id": to_object_id(payload.recipe_id)})
    if not rec:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe = Recipe.model_validate({
        "name": rec.get("name", "Recipe"),
        "components": rec.get("components", []),
        "yield_kg": rec.get("yield_kg"),
    })
    metrics = compute_recipe_metrics(recipe)
    return metrics

# Label generation (MVP)
class LabelRequest(BaseModel):
    recipe_id: str

@app.post("/api/labels")
def generate_label(payload: LabelRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    rec = db["recipe"].find_one({"_id": to_object_id(payload.recipe_id)})
    if not rec:
        raise HTTPException(status_code=404, detail="Recipe not found")

    comps = rec.get("components", [])
    names: List[str] = []
    allergens: List[str] = []
    total_g = sum([c.get("grams", 0) for c in comps]) or 1

    nutrition = {
        "energy_kcal": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "sugars_g": 0.0,
        "fat_g": 0.0,
        "sat_fat_g": 0.0,
        "fiber_g": 0.0,
        "salt_g": 0.0,
    }

    for c in comps:
        ing = db["ingredient"].find_one({"_id": to_object_id(c.get("ingredient_id"))})
        if not ing:
            continue
        names.append(ing.get("name"))
        allergens += ing.get("allergens", [])
        g = c.get("grams", 0)
        factor = g / total_g
        nutrition["energy_kcal"] += factor * ing.get("energy_kcal", 0)
        nutrition["protein_g"] += factor * ing.get("protein_g", 0)
        nutrition["carbs_g"] += factor * ing.get("carbs_g", 0)
        nutrition["sugars_g"] += factor * ing.get("sugars_g", 0)
        nutrition["fat_g"] += factor * ing.get("fat_g", 0)
        nutrition["sat_fat_g"] += factor * ing.get("sat_fat_g", 0)
        nutrition["fiber_g"] += factor * ing.get("fiber_g", 0)
        nutrition["salt_g"] += factor * ing.get("salt_g", 0)

    allergens = sorted(list(set(allergens)))

    label = {
        "product": rec.get("name"),
        "ingredients": names,
        "allergens": allergens,
        "nutrition_per_100g": {k: round(v, 2) for k, v in nutrition.items()},
    }
    return label

# CRM: Customers (MVP)
class CreateCustomerRequest(Customer):
    pass

@app.post("/api/customers")
def create_customer(payload: CreateCustomerRequest):
    _id = create_document("customer", payload)
    return {"id": _id}

@app.get("/api/customers")
def list_customers(limit: Optional[int] = 200):
    docs = get_documents("customer", {}, limit or 200)
    for d in docs:
        if "_id" in d:
            d["id"] = str(d.pop("_id"))
    return docs

# Orders (MVP)
class CreateOrderRequest(Order):
    pass

@app.post("/api/orders")
def create_order(payload: CreateOrderRequest):
    # compute total if not provided
    total = 0.0
    for it in payload.items:
        total += it.qty_kg * it.unit_price
    data = payload.model_dump()
    data["total"] = round(total, 2)
    _id = create_document("order", data)
    return {"id": _id, "total": data["total"]}

@app.get("/api/orders")
def list_orders(limit: Optional[int] = 200):
    docs = get_documents("order", {}, limit or 200)
    for d in docs:
        if "_id" in d:
            d["id"] = str(d.pop("_id"))
    return docs

# Inventory expiry notifications (MVP)
@app.get("/api/inventory/expiring")
def expiring_inventory(days: int = 7):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)
    items = list(db["inventoryitem"].find({"expiry_date": {"$lte": cutoff}}))
    for it in items:
        it["id"] = str(it.pop("_id", ""))
    return items

# Tutorials (static seed)
@app.get("/api/tutorials")
def tutorials():
    return [
        {"title": "Bilanciamento gelato: basi", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "category": "tecnica"},
        {"title": "Etichette conformi Reg. UE", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "category": "normativa"},
    ]

# Simple CSV export for recipes
@app.get("/api/export/recipes.csv")
def export_recipes_csv():
    docs = list(db["recipe"].find({})) if db is not None else []
    headers = ["id", "name", "components_count", "tags"]
    lines = [",".join(headers)]
    for d in docs:
        rid = str(d.get("_id", ""))
        name = (d.get("name", "") or "").replace(",", " ")
        comps = len(d.get("components", []))
        tags = ";".join(d.get("tags", []))
        lines.append(f"{rid},{name},{comps},{tags}")
    csv = "\n".join(lines)
    return Response(content=csv, media_type="text/csv")

# Simple CSV export for labels (product, ingredients, allergens)
@app.get("/api/export/labels.csv")
def export_labels_csv():
    if db is None:
        return Response(content="id,product,ingredients,allergens\n", media_type="text/csv")
    docs = list(db["recipe"].find({}))
    lines = ["id,product,ingredients,allergens"]
    for rec in docs:
        rid = str(rec.get("_id", ""))
        comps = rec.get("components", [])
        names: List[str] = []
        allergens: List[str] = []
        for c in comps:
            ing = db["ingredient"].find_one({"_id": to_object_id(c.get("ingredient_id"))})
            if ing:
                names.append(ing.get("name", ""))
                allergens += ing.get("allergens", [])
        line = f"{rid},{(rec.get('name','') or '').replace(',', ' ')},{'|'.join(names)},{'|'.join(sorted(set(allergens)))}"
        lines.append(line)
    csv = "\n".join(lines)
    return Response(content=csv, media_type="text/csv")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
