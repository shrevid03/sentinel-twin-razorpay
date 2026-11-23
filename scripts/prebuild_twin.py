"""Train the seeded twin once at build time and save it, so a small server starts in seconds.

    python scripts/prebuild_twin.py data/twin.pkl

The server loads it when TWIN_PREBUILT points at the file and TWIN_SEED / GAT_EPOCHS match; otherwise it trains.
"""
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.twin import Twin  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/twin.pkl")
seed, epochs = int(os.getenv("TWIN_SEED", "3")), int(os.getenv("GAT_EPOCHS", "120"))
t = Twin(seed=seed)
t.train(epochs=epochs)
t.prebuilt_for = (seed, epochs)
t.lock = None  # locks don't pickle; the server gives the loaded twin a fresh one
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "wb") as f:
    pickle.dump(t, f, protocol=pickle.HIGHEST_PROTOCOL)
print(f"saved {out} ({out.stat().st_size // 1024} KB): seed {seed}, {epochs} epochs, test AUC {t.metrics['test_auc']}")
