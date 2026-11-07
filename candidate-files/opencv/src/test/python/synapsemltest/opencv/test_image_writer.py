# Copyright (C) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in project root for information.

import tempfile
import unittest
from pathlib import Path


class ImageWriterTests(unittest.TestCase):
    def test_transformed_images_are_written_with_real_pixels(self):
        from PIL import Image
        from pyspark.ml.image import ImageSchema
        from pyspark.sql.types import StringType, StructType
        from synapse.ml.core.init_spark import init_spark
        from synapse.ml.opencv import ImageTransformer
        import synapse.ml.io.IOImplicits  # Registers DataFrameWriter.image.

        spark = init_spark()
        schema = StructType.fromJson(ImageSchema.imageSchema.jsonValue()).add(
            "filenames", StringType(), True
        )
        rows = [
            (("source-a.png", 1, 2, 3, 16, bytearray([3, 2, 1, 6, 5, 4])), "a.png"),
            (("source-b.png", 1, 2, 3, 16, bytearray([3, 2, 1, 6, 5, 4])), "nested/b.png"),
        ]
        images = spark.createDataFrame(rows, schema).repartition(2)
        transformed = (
            ImageTransformer().setInputCol("image").setOutputCol("flipped").flip(1)
        ).transform(images).select("flipped", "filenames")
        with tempfile.TemporaryDirectory(prefix="image-writer-") as root:
            output = str(Path(root) / "images")
            transformed.write.image().option("imageCol", "flipped").save(output)
            paths = sorted(Path(output).rglob("*.png"))
            self.assertEqual(
                [path.relative_to(output).as_posix() for path in paths],
                ["a.png", "nested/b.png"],
            )
            for path in paths:
                with Image.open(path) as decoded:
                    self.assertEqual(decoded.size, (2, 1))
                    self.assertEqual(
                        list(decoded.convert("RGB").getdata()), [(4, 5, 6), (1, 2, 3)]
                    )
            self.assertEqual(
                spark.read.image().option("recursiveFileLookup", "true").load(output).count(),
                len(rows),
            )
            self.assertEqual(images.count(), len(rows))

    def test_alpha_image_roundtrip_preserves_color_and_transparency(self):
        from PIL import Image
        from pyspark.ml.image import ImageSchema
        from pyspark.sql.types import StringType, StructType
        from synapse.ml.core.init_spark import init_spark
        import synapse.ml.io.IOImplicits

        spark = init_spark()
        schema = StructType.fromJson(ImageSchema.imageSchema.jsonValue()).add(
            "filenames", StringType(), True
        )
        pixels = bytearray([3, 2, 1, 64, 6, 5, 4, 127])
        images = spark.createDataFrame(
            [(("alpha.png", 1, 2, 4, 24, pixels), "alpha.png")], schema
        )
        with tempfile.TemporaryDirectory(prefix="image-alpha-") as root:
            output = str(Path(root) / "images")
            images.write.image().save(output)
            with Image.open(Path(output) / "alpha.png") as decoded:
                self.assertEqual(
                    list(decoded.convert("RGBA").getdata()),
                    [(1, 2, 3, 64), (4, 5, 6, 127)],
                )
            reloaded = spark.read.image().load(output).select("image.data").first()[0]
            self.assertEqual(bytes(reloaded), bytes(pixels))


if __name__ == "__main__":
    unittest.main()
