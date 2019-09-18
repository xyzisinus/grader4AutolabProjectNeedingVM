# Start with empty ubuntu machine
FROM ubuntu:18.04
# use python image?

MAINTAINER "jboles@cmu.edu"

# Install dependancies
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -yq \
    vim \
    tzdata \
    python3-pip \
    python3-dev \
    iputils-ping \
    openssh-server \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir /var/run/grader

# Change to working directory
RUN mkdir /opt/grader
WORKDIR /opt/grader

# Define additional metadata for our image.
VOLUME /var/lib/docker

# Create virtualenv to link dependancies
RUN pip3 install boto3
RUN pip3 install PyYAML

WORKDIR /opt/grader
ADD grader.py .
ADD config_defaults.yaml .

ENTRYPOINT ["python3", "./grader.py"]

# for diving into the container to look around
# ENTRYPOINT ["sleep", "10000"]
