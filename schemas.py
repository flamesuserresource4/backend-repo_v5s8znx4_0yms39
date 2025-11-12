"""
Database Schemas for Gelato Pro Suite

Each Pydantic model represents a collection in MongoDB. The collection name is the
lowercase of the class name (e.g., Recipe -> "recipe").
"""

from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Core users (basic for multi-user access; authentication can be added later)
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    role: str = Field("user", description="Role: admin, manager, user")
    is_active: bool = Field(True)

# Ingredients with techno-functional properties
class Ingredient(BaseModel):
    name: str
    category: str = Field(..., description="e.g., milk, cream, sugar, stabilizer, flavor")
    supplier: Optional[str] = None
    allergens: List[str] = Field(default_factory=list, description="e.g., ['milk', 'nuts']")
    cost_per_kg: float = Field(0.0, ge=0, description="Cost in currency per kg")
    # Technological composition (percentage on 100 g)
    total_solids_pct: float = Field(0.0, ge=0, le=100)
    fat_pct: float = Field(0.0, ge=0, le=100)
    sugar_pct: float = Field(0.0, ge=0, le=100)
    lactose_pct: float = Field(0.0, ge=0, le=100)
    stabilizer_pct: float = Field(0.0, ge=0, le=100)
    sweetness_equiv: float = Field(1.0, ge=0, description="Relative sweetness vs sucrose = 1.0")
    # Nutrition (per 100 g)
    energy_kcal: float = Field(0.0, ge=0)
    protein_g: float = Field(0.0, ge=0)
    carbs_g: float = Field(0.0, ge=0)
    sugars_g: float = Field(0.0, ge=0)
    fat_g: float = Field(0.0, ge=0)
    sat_fat_g: float = Field(0.0, ge=0)
    fiber_g: float = Field(0.0, ge=0)
    salt_g: float = Field(0.0, ge=0)

class RecipeComponent(BaseModel):
    ingredient_id: str
    grams: float = Field(..., ge=0)

class Recipe(BaseModel):
    name: str
    components: List[RecipeComponent] = Field(default_factory=list)
    yield_kg: Optional[float] = Field(None, ge=0, description="Target batch weight in kg")
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    author_id: Optional[str] = None

class BatchLot(BaseModel):
    recipe_id: str
    lot_code: str
    date_produced: datetime
    expiry_date: datetime
    quantity_kg: float = Field(..., ge=0)

class InventoryItem(BaseModel):
    ingredient_id: str
    lot_code: Optional[str] = None
    qty_kg: float = Field(..., ge=0)
    expiry_date: Optional[datetime] = None
    cost_per_kg: Optional[float] = Field(None, ge=0)
    supplier: Optional[str] = None
    last_updated: Optional[datetime] = None

class Customer(BaseModel):
    business_name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    vat_number: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class OrderItem(BaseModel):
    recipe_id: str
    qty_kg: float = Field(..., ge=0)
    unit_price: float = Field(0.0, ge=0)

class Order(BaseModel):
    customer_id: str
    status: str = Field("draft", description="draft, confirmed, in_production, completed, cancelled")
    due_date: Optional[datetime] = None
    items: List[OrderItem] = Field(default_factory=list)
    total: Optional[float] = Field(None, ge=0)

class Notification(BaseModel):
    type: str = Field(..., description="e.g., expiry, order, system")
    message: str
    date: datetime
    read: bool = False

class Tutorial(BaseModel):
    title: str
    url: str
    category: Optional[str] = None

# Minimal analytics/report filters
class ReportFilter(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    group_by: Optional[str] = Field(None, description="e.g., day, week, month")
