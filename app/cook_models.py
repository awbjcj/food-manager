from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Effort = Literal["easy", "medium", "hard"]


class SelectedItems(BaseModel):
    item_ids: list[int]
    rationale: str = ""


class RecipeIngredient(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None


class RecipeCandidate(BaseModel):
    title: str
    cuisine: str
    source_url: Optional[str] = None
    ingredients: list[RecipeIngredient]
    method_gist: str
    deliciousness: float = Field(ge=0.0, le=1.0, default=0.5)


class RecipeCandidates(BaseModel):
    candidates: list[RecipeCandidate]


class NutritionScore(BaseModel):
    health_score: int = Field(ge=0, le=100)
    effort: Effort
    est_minutes: int = Field(ge=1, le=600)
    rationale: str


class NutritionScores(BaseModel):
    scores: list[NutritionScore]


class ScoredCandidate(BaseModel):
    recipe: RecipeCandidate
    nutrition: NutritionScore
    expiry_use: float = Field(ge=0.0, le=1.0)
    final_score: float
    shopping_list: list[str] = Field(default_factory=list)
