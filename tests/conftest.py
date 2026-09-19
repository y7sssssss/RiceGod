"""Explicit test resources; importing production modules opens no databases."""

import pytest

from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


@pytest.fixture(scope="session")
def catalog():
    repository = RecipeGraphRepository()
    yield repository
    repository.client.close()


@pytest.fixture(scope="session")
def local():
    return RecipeRepository()
