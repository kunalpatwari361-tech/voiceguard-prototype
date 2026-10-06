"""Smart Learning (feature 31): federated averaging across phones.

Each phone trains a small classifier on its own call fingerprints and uploads only the model weights;
the server averages them (FedAvg). Audio never leaves the phone. This simulation uses synthetic
fingerprint vectors with a different phone codec / noise profile per client (non-IID), which is
exactly the situation where federated learning helps.
"""
import numpy as np

FEATURES = ["jitter_pct", "shimmer_pct", "pause_cv", "noise_floor_dbfs", "breaths_per_min",
            "f2_jump_rate", "deepfake_prob"]


def _client_data(rng, n, shift, fake_ratio):
    y = (rng.random(n) < fake_ratio).astype(float)
    real = np.array([0.9, 5.0, 0.55, -50, 12, 0.08, 0.2])
    fake = np.array([0.35, 2.2, 0.25, -78, 2, 0.17, 0.75])
    scale = np.array([0.3, 1.5, 0.15, 8, 4, 0.04, 0.2])
    mu = np.where(y[:, None] == 1, fake, real) + shift
    X = mu + rng.normal(0, 1, (n, len(FEATURES))) * scale * 1.3
    return X, y


def _norm(X, m, s):
    return (X - m) / s


def _sgd(w, X, y, epochs=5, lr=0.1):
    for _ in range(epochs):
        p = 1 / (1 + np.exp(-(X @ w[:-1] + w[-1])))
        g = p - y
        w = w - lr * np.concatenate([X.T @ g / len(y), [g.mean()]])
    return w


def _acc(w, X, y):
    return float(((X @ w[:-1] + w[-1] > 0) == (y == 1)).mean())


def simulate(rounds: int = 8, n_clients: int = 5, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    codecs = ["AMR-NB (2G/3G)", "AMR-WB (VoLTE)", "EVS (VoLTE HD)", "Opus (WhatsApp)", "G.711 (landline)"]
    clients = []
    for i in range(n_clients):
        shift = rng.normal(0, 1, len(FEATURES)) * np.array([0.1, 0.6, 0.05, 6, 2, 0.02, 0.08])
        X, y = _client_data(rng, int(rng.integers(40, 160)), shift, fake_ratio=float(rng.uniform(0.15, 0.6)))
        clients.append({"X": X, "y": y, "codec": codecs[i % len(codecs)]})
    Xt, yt = _client_data(rng, 800, np.zeros(len(FEATURES)), 0.4)
    allX = np.vstack([c["X"] for c in clients])
    m, s = allX.mean(0), allX.std(0) + 1e-9
    Xt = _norm(Xt, m, s)

    local_only = []
    for c in clients:
        w = _sgd(np.zeros(len(FEATURES) + 1), _norm(c["X"], m, s), c["y"], epochs=40)
        local_only.append(_acc(w, Xt, yt))

    w_global = np.zeros(len(FEATURES) + 1)
    history = []
    for r in range(1, rounds + 1):
        updates, sizes = [], []
        for c in clients:
            updates.append(_sgd(w_global.copy(), _norm(c["X"], m, s), c["y"], epochs=5))
            sizes.append(len(c["y"]))
        w_global = np.average(np.stack(updates), axis=0, weights=sizes)
        history.append({"round": r, "accuracy": round(_acc(w_global, Xt, yt), 4)})

    return {
        "rounds": history,
        "clients": [{"id": i + 1, "codec": c["codec"], "samples": int(len(c["y"])),
                     "local_only_accuracy": round(local_only[i], 4)} for i, c in enumerate(clients)],
        "final_accuracy": history[-1]["accuracy"],
        "features": FEATURES,
        "privacy": "Only 8 model weights per phone are shared each round. No audio, transcript or number leaves the phone.",
        "privacy_hi": "हर राउंड में फ़ोन से सिर्फ़ 8 मॉडल-वेट भेजे जाते हैं। कोई आवाज़, बातचीत या नंबर फ़ोन से बाहर नहीं जाता।",
    }
