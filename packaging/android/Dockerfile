# syntax=docker/dockerfile:1.7
# KADRAJ Android derleme ortamı: pyside6-android-deploy (buildozer + python-for-android, "qt" bootstrap).
# İmaj adı: kadraj-android-build. SDK/NDK ve buildozer önbelleği /home/builder altında tutulur;
# adımlar arasında yeniden indirmemek için oraya adlandırılmış bir birim bağlanır (kadraj-android-sdk).
# Ayrıntılar: docs/steps/MOBILE_1.md
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# İsteğe bağlı: TLS'i yeniden imzalayan bir vekil sunucunun arkasında derlenirken iki BuildKit gizlisi verilir:
#   bootstrap_ca: apt'nin HTTPS ile ilk indirmesi için tam CA paketi (imajda henüz ca-certificates yok),
#   extra_ca:     sisteme ve Java'ya eklenecek vekil CA sertifikası (tek sertifika).
# Örnek: docker build --secret id=bootstrap_ca,src=ca-bundle.crt --secret id=extra_ca,src=proxy-ca.crt ...
# Verilmezse bu kısımlar atlanır ve imaj olağan şekilde kurulur.
RUN --mount=type=secret,id=bootstrap_ca,required=false \
    --mount=type=secret,id=extra_ca,required=false \
    set -eux; \
    if [ -s /run/secrets/bootstrap_ca ]; then \
        install -m 0644 /run/secrets/bootstrap_ca /tmp/bootstrap-ca.crt; \
        sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
        echo 'Acquire::https::CAInfo "/tmp/bootstrap-ca.crt";' > /etc/apt/apt.conf.d/99bootstrap-ca; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates ca-certificates-java openjdk-17-jdk-headless \
        python3 python3-venv python3-dev python3-pip \
        git zip unzip curl patch file lld ccache \
        build-essential autoconf automake autopoint libtool libltdl-dev pkg-config cmake ninja-build gettext \
        libffi-dev libssl-dev zlib1g-dev libbz2-dev liblzma-dev libsqlite3-dev libncurses-dev libreadline-dev \
        libglib2.0-0t64 libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3; \
    if [ -s /run/secrets/extra_ca ]; then \
        install -m 0644 /run/secrets/extra_ca /usr/local/share/ca-certificates/extra-ca.crt; \
        update-ca-certificates; \
    fi; \
    rm -f /etc/apt/apt.conf.d/99bootstrap-ca /tmp/bootstrap-ca.crt; \
    rm -rf /var/lib/apt/lists/*

# buildozer root olarak çalışmak istemez; derleme "builder" kullanıcısıyla yapılır.
# Ubuntu 24.04 imajında 1000 numaralı "ubuntu" kullanıcısı var; onu yeniden adlandırıyoruz.
RUN set -eux; \
    usermod -l builder -d /home/builder -m ubuntu; \
    groupmod -n builder ubuntu; \
    python3 -m venv /opt/venv; \
    chown -R builder:builder /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BUILDOZER_WARN_ON_ROOT=0

USER builder
WORKDIR /home/builder

# Masaüstü PySide6 tekerleği yalnız pyside6-android-deploy betiği için gerekir (APK'ya Android tekerleği girer).
# Sürümler PySide6 6.12'nin deploy_lib/default.spec ve requirements-android.txt dosyalarıyla aynı.
ARG PYSIDE_VERSION=6.12.0
RUN pip install --no-cache-dir \
        "PySide6-Essentials==${PYSIDE_VERSION}" \
        "buildozer==1.6.0" "cython==0.29.33" \
        jinja2 pkginfo tqdm "packaging==24.1" setuptools wheel appdirs sh colorama toml

CMD ["bash"]
