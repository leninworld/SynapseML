"""Verify loaded real wheels/JARs; no model-specific behavior or mocked transport."""
import hashlib
import importlib
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from validation_runtime import require

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

def verify_installed_python(expected):
    proofs = {}
    for name, sha in expected.items():
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        require(path.is_relative_to(Path(sys.prefix).resolve()),
                f"Module outside installed environment: {name}: {path}")
        actual = digest(path)
        require(actual == sha, f"Installed module hash mismatch: {name}")
        proofs[name] = {"path": str(path), "sha256": actual}
    return proofs

def verify_jvm(spark, expected):
    loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
    proofs = {}
    for name, sha in expected.items():
        cls = loader.loadClass(name)
        location = cls.getProtectionDomain().getCodeSource().getLocation().toString()
        parsed = urlparse(location)
        require(parsed.scheme == "file" and not parsed.netloc,
                f"Nonlocal code source: {name}: {location}")
        path = Path(unquote(parsed.path)).resolve(strict=True)
        require(path.is_file() and path.suffix == ".jar",
                f"Expected built JAR, not class directory: {name}: {path}")
        actual = digest(path)
        require(actual == sha, f"Loaded JAR hash mismatch: {name}")
        proofs[name] = {"path": str(path), "sha256": actual}
    return proofs

