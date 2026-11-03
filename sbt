#!/bin/sh
exec /opt/java/openjdk/bin/java -Xms2G -Xmx4G -Xss5M -Duser.timezone=GMT -Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp -Dsbt.supershell=false -Dsbt.log.noformat=true -jar /opt/bootstrap/sbt-launch.jar "$@"
