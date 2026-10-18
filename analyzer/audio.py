"""analyze: Essentia audio features and embeddings, computed from the MP3 itself."""
import json
import os

import numpy as np

from . import config
from .tags import style_tags

MODEL_VERSION = "discogs-effnet-bs64-1"
STYLE_FLOOR = 0.05

PITCH_CLASSES = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6,
    "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}


class Analyzer:
    """Holds the loaded TensorFlow graphs; build one per process."""

    def __init__(self):
        import essentia
        import essentia.standard as es

        essentia.log.infoActive = False
        essentia.log.warningActive = False
        self.es = es
        models = config.MODELS_DIR
        self.effnet = es.TensorflowPredictEffnetDiscogs(
            graphFilename=os.path.join(models, "discogs-effnet-bs64-1.pb"), output="PartitionedCall:1"
        )
        self.style_head = es.TensorflowPredict2D(
            graphFilename=os.path.join(models, "genre_discogs400-discogs-effnet-1.pb"),
            input="serving_default_model_Placeholder",
            output="PartitionedCall:0",
        )
        with open(os.path.join(models, "genre_discogs400-discogs-effnet-1.json")) as f:
            self.style_labels = json.load(f)["classes"]

    def analyze(self, path):
        es = self.es
        audio = es.MonoLoader(filename=path, sampleRate=44100)()
        bpm, _, bpm_confidence, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)
        key, scale, key_strength = es.KeyExtractor()(audio)
        danceability, _ = es.Danceability()(audio)

        stereo, sample_rate, *_ = es.AudioLoader(filename=path)()
        _, _, loudness, _ = es.LoudnessEBUR128(sampleRate=sample_rate)(stereo)

        audio16k = es.Resample(inputSampleRate=44100, outputSampleRate=16000, quality=4)(audio)
        frames = self.effnet(audio16k)
        style_probs = self.style_head(frames).mean(axis=0)
        embedding = frames.mean(axis=0)
        embedding = embedding / (np.linalg.norm(embedding) or 1.0)

        styles = {
            label: round(float(p), 3)
            for label, p in zip(self.style_labels, style_probs)
            if p >= STYLE_FLOOR
        }
        return {
            "bpm": float(bpm),
            "bpm_confidence": float(bpm_confidence),
            "key": PITCH_CLASSES[key],
            "mode": scale,
            "key_strength": float(key_strength),
            "loudness": float(loudness),
            "danceability": float(danceability),
            "styles": styles,
            "sound_embedding": embedding.astype(np.float32),
        }


_worker = None


def init_worker():
    """ProcessPoolExecutor initializer: load the models once per process."""
    global _worker
    _worker = Analyzer()


def analyze_in_worker(path):
    features = _worker.analyze(path)
    features["sound_embedding"] = features["sound_embedding"].tolist()
    features["style_tags"] = style_tags(features["styles"])
    features["model_version"] = MODEL_VERSION
    return features
