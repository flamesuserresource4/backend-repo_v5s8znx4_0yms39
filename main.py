import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
    InventoryMovement,
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

# Inventory advanced module

def _notify_expiring(item: dict, days_threshold: int = 7):
    try:
        expiry = item.get("expiry_date")
        if not expiry:
            return
        now = datetime.utcnow()
        if isinstance(expiry, str):
            try:
                expiry = datetime.fromisoformat(expiry)
            except Exception:
                return
        if expiry <= now + timedelta(days=days_threshold):
            message = f"Lotto {item.get('lot_code','')} di ingrediente {item.get('ingredient_id')} in scadenza il {expiry.date().isoformat()}"
            notif = Notification(type="expiry", message=message, date=now, read=False)
            create_document("notification", notif)
    except Exception:
        pass

class CreateMovementRequest(InventoryMovement):
    pass

@app.post("/api/inventory/movements")
def create_movement(payload: CreateMovementRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    data = payload.model_dump()
    mtype = data.get("type")
    if mtype not in ("in", "out"):
        raise HTTPException(status_code=400, detail="Movement type must be 'in' or 'out'")

    # Save movement history
    mid = create_document("inventorymovement", data)

    # Update or create inventory item per ingredient+lot
    lot_filter = {"ingredient_id": data["ingredient_id"], "lot_code": data["lot_code"]}
    item = db["inventoryitem"].find_one(lot_filter)

    if mtype == "in":
        if item:
            new_qty = float(item.get("qty_kg", 0)) + float(data["qty_kg"])
            update = {
                "$set": {
                    "qty_kg": new_qty,
                    "expiry_date": data.get("expiry_date") or item.get("expiry_date"),
                    "cost_per_kg": data.get("cost_per_kg", item.get("cost_per_kg")),
                    "supplier": data.get("supplier", item.get("supplier")),
                    "last_updated": datetime.utcnow(),
                }
            }
            db["inventoryitem"].update_one(lot_filter, update)
        else:
            inv = InventoryItem(
                ingredient_id=data["ingredient_id"],
                lot_code=data["lot_code"],
                qty_kg=float(data["qty_kg"]),
                expiry_date=data.get("expiry_date"),
                cost_per_kg=data.get("cost_per_kg"),
                supplier=data.get("supplier"),
                last_updated=datetime.utcnow(),
            )
            create_document("inventoryitem", inv)
    else:  # out
        if not item:
            raise HTTPException(status_code=404, detail="Inventory lot not found")
        current = float(item.get("qty_kg", 0))
        qty = float(data["qty_kg"])
        if qty > current:
            raise HTTPException(status_code=400, detail="Not enough stock for this lot")
        new_qty = current - qty
        update = {"$set": {"qty_kg": new_qty, "last_updated": datetime.utcnow()}}
        db["inventoryitem"].update_one(lot_filter, update)

    # Create notification if expiring soon
    it = db["inventoryitem"].find_one(lot_filter)
    if it:
        _notify_expiring(it)

    return {"movement_id": mid}

@app.get("/api/inventory/items")
def list_inventory_items(limit: Optional[int] = 500):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    items = get_documents("inventoryitem", {}, limit or 500)
    now = datetime.utcnow()
    result = []
    for it in items:
        it_id = str(it.pop("_id", ""))
        expiry = it.get("expiry_date")
        days_to_expiry = None
        status = "ok"
        if expiry:
            if isinstance(expiry, str):
                try:
                    expiry = datetime.fromisoformat(expiry)
                except Exception:
                    expiry = None
            if expiry:
                delta = (expiry - now).days
                days_to_expiry = delta
                if delta < 0:
                    status = "expired"
                elif delta <= 7:
                    status = "soon"
        # fetch ingredient name
        ing = db["ingredient"].find_one({"_id": to_object_id(it.get("ingredient_id"))}) if it.get("ingredient_id") else None
        result.append({
            "id": it_id,
            "ingredient_id": it.get("ingredient_id"),
            "ingredient_name": ing.get("name") if ing else None,
            "lot_code": it.get("lot_code"),
            "qty_kg": round(float(it.get("qty_kg", 0)), 3),
            "expiry_date": expiry.isoformat() if isinstance(expiry, datetime) else (it.get("expiry_date") if it.get("expiry_date") else None),
            "days_to_expiry": days_to_expiry,
            "status": status,
            "cost_per_kg": it.get("cost_per_kg"),
            "supplier": it.get("supplier"),
        })
    return result

@app.get("/api/inventory/report")
def inventory_report(start_date: Optional[str] = None, end_date: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    try:
        start = datetime.fromisoformat(start_date) if start_date else datetime.utcnow() - timedelta(days=30)
        end = datetime.fromisoformat(end_date) if end_date else datetime.utcnow()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO format.")

    # Aggregate movements
    q = {"movement_date": {"$gte": start, "$lte": end}}
    moves = list(db["inventorymovement"].find(q))

    summary: Dict[str, Dict[str, float]] = {}
    for m in moves:
        ing_id = m.get("ingredient_id")
        if ing_id not in summary:
            summary[ing_id] = {"in_qty": 0.0, "out_qty": 0.0}
        if m.get("type") == "in":
            summary[ing_id]["in_qty"] += float(m.get("qty_kg", 0))
        else:
            summary[ing_id]["out_qty"] += float(m.get("qty_kg", 0))

    # Current stock per ingredient
    items = list(db["inventoryitem"].find({}))
    stock: Dict[str, float] = {}
    for it in items:
        ing_id = it.get("ingredient_id")
        stock[ing_id] = stock.get(ing_id, 0.0) + float(it.get("qty_kg", 0))

    report = []
    for ing_id, sums in summary.items():
        ing = db["ingredient"].find_one({"_id": to_object_id(ing_id)})
        report.append({
            "ingredient_id": ing_id,
            "ingredient_name": ing.get("name") if ing else None,
            "in_qty": round(sums.get("in_qty", 0.0), 3),
            "out_qty": round(sums.get("out_qty", 0.0), 3),
            "net_change": round(sums.get("in_qty", 0.0) - sums.get("out_qty", 0.0), 3),
            "current_stock": round(stock.get(ing_id, 0.0), 3),
        })

    return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "data": report}

# Inventory exports
@app.get("/api/export/inventory.csv")
def export_inventory_csv():
    if db is None:
        return Response(content="id,ingredient,ingredient_id,lot_code,qty_kg,expiry_date,days_to_expiry,cost_per_kg,supplier\n", media_type="text/csv")
    items = list(db["inventoryitem"].find({}))
    now = datetime.utcnow()
    lines = ["id,ingredient,ingredient_id,lot_code,qty_kg,expiry_date,days_to_expiry,cost_per_kg,supplier"]
    for it in items:
        iid = str(it.get("_id", ""))
        ing = db["ingredient"].find_one({"_id": to_object_id(it.get("ingredient_id"))}) if it.get("ingredient_id") else None
        expiry = it.get("expiry_date")
        if isinstance(expiry, datetime):
            expiry_str = expiry.date().isoformat()
            days = (expiry - now).days
        else:
            expiry_str = str(expiry) if expiry else ""
            try:
                dt = datetime.fromisoformat(expiry) if expiry else None
                days = (dt - now).days if dt else ""
            except Exception:
                days = ""
        line = f"{iid},{(ing.get('name') if ing else '')},{it.get('ingredient_id','')},{it.get('lot_code','')},{round(float(it.get('qty_kg',0)),3)},{expiry_str},{days},{it.get('cost_per_kg','')},{(it.get('supplier','') or '')}"
        lines.append(line)
    csv = "\n".join(lines)
    return Response(content=csv, media_type="text/csv")

@app.get("/api/export/movements.csv")
def export_movements_csv():
    if db is None:
        return Response(content="id,type,ingredient_id,lot_code,qty_kg,movement_date,reason,expiry_date,cost_per_kg,supplier,note\n", media_type="text/csv")
    moves = list(db["inventorymovement"].find({}))
    lines = ["id,type,ingredient_id,lot_code,qty_kg,movement_date,reason,expiry_date,cost_per_kg,supplier,note"]
    for m in moves:
        mid = str(m.get("_id", ""))
        mvdate = m.get("movement_date")
        if isinstance(mvdate, datetime):
            mvdate = mvdate.isoformat()
        exp = m.get("expiry_date")
        if isinstance(exp, datetime):
            exp = exp.date().isoformat()
        line = f"{mid},{m.get('type','')},{m.get('ingredient_id','')},{m.get('lot_code','')},{round(float(m.get('qty_kg',0)),3)},{mvdate},{m.get('reason','')},{exp},{m.get('cost_per_kg','')},{(m.get('supplier','') or '')},{(m.get('note','') or '').replace(',', ' ')}"
        lines.append(line)
    csv = "\n".join(lines)
    return Response(content=csv, media_type="text/csv")

@app.get("/api/export/inventory.pdf")
def export_inventory_pdf():
    # Generate a simple PDF snapshot of current inventory
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from io import BytesIO

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 40, "Report Inventario Ingredienti")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 60, datetime.utcnow().strftime("Generato il %Y-%m-%d %H:%M UTC"))

    y = height - 90
    c.setFont("Helvetica-Bold", 10)
    headers = ["Ingrediente", "Lotto", "Qta (kg)", "Scadenza", "Stato"]
    x_positions = [40, 220, 360, 430, 510]
    for x, h in zip(x_positions, headers):
        c.drawString(x, y, h)
    y -= 16
    c.setFont("Helvetica", 10)

    items = list(db["inventoryitem"].find({}))
    now = datetime.utcnow()
    total_kg = 0.0
    for it in items:
        if y < 60:
            c.showPage()
            y = height - 60
        ing = db["ingredient"].find_one({"_id": to_object_id(it.get("ingredient_id"))}) if it.get("ingredient_id") else None
        name = ing.get("name") if ing else ""
        lot = it.get("lot_code", "")
        qty = round(float(it.get("qty_kg", 0.0)), 3)
        total_kg += qty
        expiry = it.get("expiry_date")
        status = ""
        if isinstance(expiry, datetime):
            days = (expiry - now).days
            expiry_str = expiry.date().isoformat()
        else:
            try:
                dt = datetime.fromisoformat(expiry) if expiry else None
                days = (dt - now).days if dt else None
                expiry_str = dt.date().isoformat() if dt else ""
            except Exception:
                days = None
                expiry_str = str(expiry) if expiry else ""
        if days is not None:
            status = "expired" if days < 0 else ("soon" if days <= 7 else "ok")
        c.drawString(x_positions[0], y, str(name)[:26])
        c.drawString(x_positions[1], y, str(lot)[:12])
        c.drawRightString(x_positions[2]+30, y, f"{qty}")
        c.drawString(x_positions[3], y, expiry_str)
        c.drawString(x_positions[4], y, status)
        y -= 14

    # footer total
    y -= 10
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, f"Totale giacenza: {round(total_kg,3)} kg")

    c.showPage()
    c.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=inventory.pdf"})

# Inventory expiry notifications (MVP existing + enrich)
@app.get("/api/inventory/expiring")
def expiring_inventory(days: int = 7):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)
    items = list(db["inventoryitem"].find({"expiry_date": {"$lte": cutoff}}))
    for it in items:
        it["id"] = str(it.pop("_id", ""))
        # also create a notification per item
        _notify_expiring(it, days_threshold=days)
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
