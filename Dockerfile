FROM python:3.10-slim

# Системные зависимости
RUN apt-get update && apt-get install -y \
    git wget gcc g++ make curl \
    && rm -rf /var/lib/apt/lists/*

# Установка Go (для других инструментов)
RUN wget https://golang.org/dl/go1.22.5.linux-amd64.tar.gz && \
    tar -C /usr/local -xzf go1.22.5.linux-amd64.tar.gz && \
    rm go1.22.5.linux-amd64.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/go"
ENV PATH="${GOPATH}/bin:${PATH}"

# ===== УДАЛЕНО: WEREWIKS (репозиторий приватный/удалён) =====

# Установка DIGI-NETRA
RUN git clone https://github.com/Kauravsrestha-Duryodhan/DIGI-NETRA.git /digi-netra && \
    cd /digi-netra && \
    pip install -r requirements.txt || true

# Установка XTRA
RUN git clone https://github.com/expl0itlab/xtra.git /xtra && \
    chmod +x /xtra/xtra.sh

# Установка CreepyEYE-Genesis
RUN git clone https://github.com/CreepyHunterX/CreepyEYE-Genesis.git /creepyeye && \
    cd /creepyeye && \
    pip install -r requirements.txt || true

# Python-инструменты
RUN pip install --no-cache-dir \
    sherlock-project \
    holehe \
    theHarvester \
    maigret

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .

CMD ["python", "app.py"]
