FROM python:3.12-slim
WORKDIR /app
COPY aquant /app/aquant
COPY configs /app/configs
RUN useradd --uid 10001 --create-home aquant
USER aquant
ENTRYPOINT ["python", "-m", "aquant"]
CMD ["--help"]
