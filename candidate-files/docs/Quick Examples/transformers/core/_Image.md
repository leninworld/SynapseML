import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';
import DocTable from "@theme/DocumentationTable";




## Image

### UnrollImage

<Tabs
defaultValue="py"
values={[
{label: `Python`, value: `py`},
{label: `Scala`, value: `scala`},
]}>
<TabItem value="py">




<!--pytest-codeblocks:cont-->

```python
from synapse.ml.image import *
from azure.storage.blob import *
# images = (spark.read.format("image")
#         .option("dropInvalid", True)
#         .load("wasbs://datasets@mmlspark.blob.core.windows.net/LIME/greyscale.jpg"))
# rit = (ResizeImageTransformer()
#         .setOutputCol("out")
#         .setHeight(15)
#         .setWidth(10))
# preprocessed = rit.transform(images)
unroll = (UnrollImage()
      .setInputCol("out")
      .setOutputCol("final"))
# unroll.transform(preprocessed).show()
```

</TabItem>
<TabItem value="scala">

```scala
import com.microsoft.azure.synapse.ml.image._
import spark.implicits._
val images = (spark.read.format("image")
    .option("dropInvalid", true)
    .load("wasbs://datasets@mmlspark.blob.core.windows.net/LIME/greyscale.jpg"))
val rit = (new ResizeImageTransformer()
    .setOutputCol("out")
    .setHeight(15)
    .setWidth(10))
val preprocessed = rit.transform(images)
val unroll = (new UnrollImage()
      .setInputCol(rit.getOutputCol)
      .setOutputCol("final"))
unroll.transform(preprocessed).show()
```

</TabItem>
</Tabs>

<DocTable className="UnrollImage"
py="synapse.ml.image.html#module-synapse.ml.image.UnrollImage"
scala="com/microsoft/azure/synapse/ml/image/UnrollImage.html"
csharp="classSynapse_1_1ML_1_1Image_1_1UnrollImage.html"
sourceLink="https://github.com/microsoft/SynapseML/blob/master/core/src/main/scala/com/microsoft/azure/synapse/ml/image/UnrollImage.scala" />


### UnrollBinaryImage

<Tabs
defaultValue="py"
values={[
{label: `Python`, value: `py`},
{label: `Scala`, value: `scala`},
]}>
<TabItem value="py">




<!--pytest-codeblocks:cont-->

```python
from synapse.ml.image import *
unroll = (UnrollBinaryImage()
      .setInputCol("input_col")
      .setOutputCol("final"))
```

</TabItem>
<TabItem value="scala">

```scala
import com.microsoft.azure.synapse.ml.image._
import spark.implicits._
val unroll = (new UnrollBinaryImage()
        .setInputCol("input_col")
        .setOutputCol("final"))
```

</TabItem>
</Tabs>

<DocTable className="UnrollBinaryImage"
py="synapse.ml.image.html#module-synapse.ml.image.UnrollBinaryImage"
scala="com/microsoft/azure/synapse/ml/image/UnrollBinaryImage.html"
csharp="classSynapse_1_1ML_1_1Image_1_1UnrollBinaryImage.html"
sourceLink="https://github.com/microsoft/SynapseML/blob/master/core/src/main/scala/com/microsoft/azure/synapse/ml/image/UnrollBinaryImage.scala" />

### SuperpixelTransformer

<Tabs
defaultValue="py"
values={[
{label: `Python`, value: `py`},
{label: `Scala`, value: `scala`},
]}>
<TabItem value="py">

<!--pytest-codeblocks:cont-->

```python
from synapse.ml.image import *

spt = (SuperpixelTransformer()
      .setInputCol("images"))
```

</TabItem>
<TabItem value="scala">

```scala
import com.microsoft.azure.synapse.ml.image._

val spt = (new SuperpixelTransformer()
      .setInputCol("images"))
```

</TabItem>
</Tabs>

<DocTable className="SuperpixelTransformer"
py="synapse.ml.lime.html#module-synapse.ml.image.SuperpixelTransformer"
scala="com/microsoft/azure/synapse/ml/image/SuperpixelTransformer.html"
csharp="classSynapse_1_1ML_1_1Image_1_1SuperpixelTransformer.html"
sourceLink="https://github.com/microsoft/SynapseML/blob/master/core/src/main/scala/com/microsoft/azure/synapse/ml/image/SuperpixelTransformer.scala" />


### Write transformed images

Spark's `format("image")` source is read-only. For batch output, import
`synapse.ml.io.IOImplicits` and use `write.image()`, or use the fully qualified
format `org.apache.spark.ml.source.image.PatchedImageFileFormat`.

Provide an image struct and a string column containing **unique relative
filenames**, including an extension supported for that image type. The default
column names are `image` and `filenames`; `imageCol` and `pathCol` select different
names. Select only those two columns before writing. Nested relative directories
are supported. Absolute paths, URI schemes, backslashes, colons and `..` segments
are rejected: do not copy the full `image.origin` URI into `filenames`. Duplicate
filenames within a task fail; filenames must also be unique across tasks because
separate task commits can replace one another's files.

This complete example creates two known pixels, flips them using the native
image transformer, writes a PNG and checks the saved pixels independently.

<!--pytest-codeblocks:cont-->

```python
import tempfile
from pathlib import Path
from PIL import Image
from pyspark.ml.image import ImageSchema
from pyspark.sql.types import StringType, StructType
from synapse.ml.opencv import ImageTransformer
import synapse.ml.io.IOImplicits

write_schema = StructType.fromJson(ImageSchema.imageSchema.jsonValue()).add(
    "filenames", StringType(), True
)
write_images = spark.createDataFrame(
    [(("synthetic.png", 1, 2, 3, 16, bytearray([3, 2, 1, 6, 5, 4])), "flipped.png")],
    write_schema,
)
flipped_images = (
    ImageTransformer().setInputCol("image").setOutputCol("flipped").flip(1)
).transform(write_images).select("flipped", "filenames")

with tempfile.TemporaryDirectory(prefix="write-images-") as directory:
    destination = str(Path(directory) / "images")
    flipped_images.write.image().option("imageCol", "flipped").save(destination)
    saved_images = list(Path(destination).rglob("*.png"))
    assert len(saved_images) == 1
    with Image.open(saved_images[0]) as saved:
        assert list(saved.convert("RGB").getdata()) == [(4, 5, 6), (1, 2, 3)]
    assert spark.read.image().load(destination).count() == 1
```

The example uses local paths for local Spark. On a cluster, use a shared
filesystem accessible to every executor and the driver.

The writer requires an output committer that commits the entire task directory,
such as Spark's default Hadoop file output committer. Default `partitionBy`
locations are supported. Custom partition locations and bucketing are not
supported: their tracked task filenames do not represent the image files named
by each row. Manifest-based committers, including the default streaming file
committer, also track individual task filenames; this example does not establish
support for streaming writes or custom cloud committers. Use batch writes with
the supported file commit protocol.
