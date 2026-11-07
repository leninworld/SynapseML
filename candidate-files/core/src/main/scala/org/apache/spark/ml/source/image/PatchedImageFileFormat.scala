// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package org.apache.spark.ml.source.image

import com.microsoft.azure.synapse.ml.core.schema.ImageSchemaUtils
import com.microsoft.azure.synapse.ml.io.image.ImageUtils
import org.apache.commons.io.IOUtils
import org.apache.hadoop.conf.Configuration
import org.apache.hadoop.fs.Path
import org.apache.hadoop.mapreduce._
import org.apache.spark.internal.Logging
import org.apache.spark.ml.image.ImageSchema
import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.catalyst.InternalRow
import org.apache.spark.sql.catalyst.encoders.ExpressionEncoder
import org.apache.spark.sql.catalyst.expressions.UnsafeRow
import org.apache.spark.sql.execution.datasources._
import org.apache.spark.sql.sources._
import org.apache.spark.sql.types._
import org.apache.spark.util.SerializableConfiguration

import java.awt.image.{BufferedImage, DataBufferByte}
import java.io.IOException
import javax.imageio.{ImageIO, ImageTypeSpecifier}
import scala.util.{Failure, Success, Try}

class PatchedImageFileFormat extends ImageFileFormat with Serializable with Logging {

  override def shortName(): String = "patchedImage"

  private def verifySchema(schema: StructType, imageCol: String, pathCol: String): Unit = {
    val names = Set(imageCol, pathCol)
    val valid = imageCol != pathCol && schema.length == names.size && schema.fieldNames.toSet == names &&
      schema(pathCol).dataType == StringType &&
      (schema(imageCol).dataType == ImageSchema.columnSchema ||
        schema(imageCol).dataType == ImageSchemaUtils.ColumnSchemaNullable)
    require(valid, s"Image data source requires an image struct '$imageCol' and a string '$pathCol'; got $schema")
  }

  override def prepareWrite(sparkSession: SparkSession,
                            job: Job,
                            options: Map[String, String],
                            dataSchema: StructType): OutputWriterFactory = {
    verifySchema(dataSchema, options.getOrElse("imageCol", "image"), options.getOrElse("pathCol", "filenames"))
    new OutputWriterFactory {
      override def newInstance(
                                path: String,
                                dataSchema: StructType,
                                context: TaskAttemptContext): OutputWriter = {
        new ImageOutputWriter(
          path,
          dataSchema.fieldIndex(options.getOrElse("imageCol", "image")),
          dataSchema.fieldIndex(options.getOrElse("pathCol", "filenames")),
          context)
      }

      override def getFileExtension(context: TaskAttemptContext): String = {
        ""
      }
    }
  }

  //This is needed due to a multi-threading bug n the jvm
  @annotation.tailrec
  private def catchFlakiness[T](times: Int)(f: => Option[T]): Option[T] = {
    Try {
      f
    } match {
      case Success(x) => x
      case Failure(exception: NullPointerException) if times >= 1 =>
        logWarning("caught null pointer exception due to jvm bug", exception)
        catchFlakiness(times - 1)(f)
      case _ => None
    }
  }

  override protected def buildReader(sparkSession: SparkSession,
                                     dataSchema: StructType,
                                     partitionSchema: StructType,
                                     requiredSchema: StructType,
                                     filters: Seq[Filter],
                                     options: Map[String, String],
                                     hadoopConf: Configuration): PartitionedFile => Iterator[InternalRow] = {
    assert(
      requiredSchema.length <= 1,
      "Image data source only produces a single data column named \"image\".")

    val broadcastedHadoopConf =
      sparkSession.sparkContext.broadcast(new SerializableConfiguration(hadoopConf))

    val imageSourceOptions = new ImageOptions(options)

    (file: PartitionedFile) => {
      val emptyUnsafeRow = new UnsafeRow(0)
      if (!imageSourceOptions.dropInvalid && requiredSchema.isEmpty) {
        Iterator(emptyUnsafeRow)
      } else {
        val origin = file.filePath
        val path = new Path(origin.toString())
        val fs = path.getFileSystem(broadcastedHadoopConf.value.value)
        val stream = fs.open(path)
        val bytes = try {
          IOUtils.toByteArray(stream)
        } finally {
          IOUtils.close(stream)
        }

        val resultOpt = catchFlakiness(5)( //scalastyle:ignore magic.number
          ImageSchema.decode(origin.toString(), bytes))
        val filteredResult = if (imageSourceOptions.dropInvalid) {
          resultOpt.toIterator
        } else {
          Iterator(resultOpt.getOrElse(ImageSchema.invalidImageRow(origin.toString())))
        }

        if (requiredSchema.isEmpty) {
          filteredResult.map(_ => emptyUnsafeRow)
        } else {
          val converter = ExpressionEncoder(requiredSchema)
          filteredResult.map(row => converter.createSerializer()(row))
        }
      }
    }
  }

  override def toString: String = "PatchedImageFileFormat"
}

class ImageOutputWriter(val path: String,
                        val imageCol: Int,
                        val pathCol: Int,
                        val context: TaskAttemptContext)
  extends OutputWriter {

  private val hconf = context.getConfiguration

  private val fs = new Path(path).getFileSystem(hconf)

  private val heightField = 1
  private val widthField = 2
  private val channelsField = 3
  private val dataField = 5
  private val grayChannels = 1
  private val colorChannels = 3
  private val alphaChannels = 4

  private def relativeOutputPath(name: String): Path = {
    val message = "Image filenames must be nonempty relative paths, for example 'image.png', without '..', " +
      "backslashes, colons or URI schemes"
    require(Option(name).exists(_.trim.nonEmpty), message)
    require(!name.contains('\\') && !name.contains(':') && !name.split("/", -1).contains(".."), message)
    val relative = new Path(name)
    val uri = relative.toUri
    require(!relative.isAbsolute && Option(uri.getScheme).isEmpty && Option(uri.getAuthority).isEmpty, message)
    val base = new Path(path).getParent.makeQualified(fs.getUri, fs.getWorkingDirectory).toUri.normalize()
    val destination = new Path(new Path(base), relative).toUri.normalize()
    val prefix = base.getPath.stripSuffix("/") + "/"
    require(destination.getScheme == base.getScheme && destination.getAuthority == base.getAuthority &&
      destination.getPath.startsWith(prefix), message)
    new Path(destination)
  }

  private def imageForWrite(row: InternalRow): BufferedImage = {
    require(!row.isNullAt(imageCol), "Image struct must not be null")
    val image = row.getStruct(imageCol, ImageSchema.columnSchema.length)
    require(Seq(heightField, widthField, channelsField, dataField).forall(i => !image.isNullAt(i)),
      "Image dimensions, channels and data must not be null")
    val height = image.getInt(heightField)
    val width = image.getInt(widthField)
    val channels = image.getInt(channelsField)
    require(Set(grayChannels, colorChannels, alphaChannels).contains(channels),
      "Image writer supports valid images with 1, 3 or 4 channels")
    val pixels = width.toLong * height.toLong
    val bytes = image.getBinary(dataField)
    require(width > 0 && height > 0 && pixels <= Int.MaxValue / channels && bytes.length == pixels * channels,
      "Image dimensions and byte length do not match")
    if (channels != alphaChannels) {
      ImageUtils.toBufferedImage(image)
    } else {
      // Spark stores BGRA; Java's TYPE_4BYTE_ABGR raster stores ABGR.
      // Keep this conversion local to the writer, preserving other ImageUtils consumers.
      val result = new BufferedImage(width, height, BufferedImage.TYPE_4BYTE_ABGR)
      val ordered = result.getRaster.getDataBuffer.asInstanceOf[DataBufferByte].getData
      bytes.indices.by(alphaChannels).foreach { offset =>
        ordered(offset) = bytes(offset + colorChannels)
        ordered(offset + 1) = bytes(offset)
        ordered(offset + 2) = bytes(offset + 1)
        ordered(offset + colorChannels) = bytes(offset + 2)
      }
      result
    }
  }

  override def write(row: InternalRow): Unit = {
    require(!row.isNullAt(pathCol), "Image filename must not be null; use a relative name such as 'image.png'")
    val outputPath = relativeOutputPath(row.getString(pathCol))
    val image = imageForWrite(row)
    val name = outputPath.getName
    val separator = name.lastIndexOf('.')
    val codec = if (separator >= 0) name.substring(separator + 1) else ""
    val imageType = ImageTypeSpecifier.createFromRenderedImage(image)
    require(codec.nonEmpty && ImageIO.getImageWriters(imageType, codec).hasNext,
      s"No ImageIO writer supports filename '$name' with this image type")
    // Duplicate names within this task must fail instead of silently replacing an earlier image.
    val os = fs.create(outputPath, false)
    try {
      if (!ImageIO.write(image, codec, os)) {
        throw new IOException(s"ImageIO could not encode image '$name'")
      }
    } finally {
      os.close()
    }
  }

  // FileSystem.get returns a cached, shared instance; its lifetime is not owned by this writer.
  override def close(): Unit = ()
}
