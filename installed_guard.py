"""A fresh Python/Spark process loads the exact installed artifact and exercises its guard."""
import hashlib
import importlib
import json
from pathlib import Path
import sys

from py4j.protocol import Py4JJavaError
from pyspark.sql import SparkSession
from pyspark.sql.types import ArrayType, StringType, StructField, StructType

from validation_runtime import require


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text())
    for row in manifest["jars"]:
        require(hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest() == row["sha256"], "JAR changed")
    spark = SparkSession.builder.master("local[2]").appName("installed-schema-parity").config(
        "spark.jars", ",".join(row["path"] for row in manifest["jars"])
    ).getOrCreate()
    try:
        require(spark.version == "3.5.0", "Wrong Spark runtime")
        for name, expected in manifest["python_modules"].items():
            module = importlib.import_module(name)
            require(hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == expected,
                    "Installed Python module differs from branch wheel: " + name)
        jvm = spark._jvm
        loader = jvm.java.lang.Thread.currentThread().getContextClassLoader()
        cls = jvm.java.lang.Class.forName(
            "com.microsoft.azure.synapse.ml.services.search.AzureSearchWriter$", True, loader)
        origin = Path(cls.getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve()
        require(origin == Path(manifest["cognitive_jar"]).resolve(), "Writer loaded from wrong JAR")
        module = cls.getField("MODULE$").get(None)
        methods = [method for method in cls.getDeclaredMethods()
                   if method.getName() == "checkSchemaParity" and len(method.getParameterTypes()) == 3]
        require(len(methods) == 1, "Exact installed private method did not resolve")
        method = methods[0]
        method.setAccessible(True)
        field = {"name": "a", "type": "Edm.String"}

        def probe(names, index_names, array=False, rejected=False):
            nested = StructType([StructField(name, StringType()) for name in names])
            payload = ArrayType(nested) if array else nested
            schema = StructType([StructField("action", StringType()), StructField("id", StringType()),
                                 StructField("payload", payload)])
            frame = spark.createDataFrame([], schema)
            index = {"name": "parity-index", "fields": [
                {"name": "id", "type": "Edm.String", "key": True},
                {"name": "payload", "type": "Collection(Edm.ComplexType)" if array else "Edm.ComplexType",
                 "fields": [dict(field, name=name) for name in index_names]}]}
            arguments = spark.sparkContext._gateway.new_array(jvm.java.lang.Object, 3)
            arguments[0], arguments[1], arguments[2] = frame._jdf.schema(), json.dumps(index), "action"
            try:
                method.invoke(module, arguments)
            except Py4JJavaError as error:
                cause = error.java_exception.getCause()
                require(rejected and cause is not None and cause.getClass().getName() == "java.lang.AssertionError",
                        "Unexpected installed guard failure")
                return {"names": names, "index_names": index_names, "array": array, "rejected": True}
            require(not rejected, "Installed guard accepted an extra nested field")
            return {"names": names, "index_names": index_names, "array": array, "rejected": False}

        controls = [probe(["a"], ["a"]), probe(["a"], ["a", "b"]),
                    probe(["a", "b"], ["a"], rejected=True),
                    probe(["a", "b"], ["a"], array=True, rejected=True)]
        output = {"passed": True, "controls": controls, "spark": spark.version,
                  "compile_scala": manifest["compile_scala"],
                  "runtime_scala": jvm.scala.util.Properties.versionNumberString(),
                  "writer_code_source": str(origin), "public_write_liveAzure": "NOT_RUN"}
        (Path(sys.argv[1]).parent / "installed-guard-result.json").write_text(json.dumps(output, indent=2))
        print(json.dumps(output))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
