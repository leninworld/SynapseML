#!/bin/sh
exec /opt/java/openjdk/bin/java -Xms1G -Xmx4G -Xss5M -Duser.timezone=GMT -Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp -Dsbt.supershell=false -Dsbt.log.noformat=true -jar /runtime/bootstrap/sbt-launch.jar "$@"
