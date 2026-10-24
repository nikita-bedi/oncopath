import os
import sys
from pathlib import Path

# Tests never depend on the shared public HAPI sandbox or network availability.
os.environ.setdefault("FHIR_LAB_REMOTE_ENABLED", "false")

# make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.data_loader import load_cohort


@pytest.fixture(scope="session")
def cohort():
    return load_cohort()
