FROM python:3.12-slim
WORKDIR /app
COPY transparent_vacancy ./transparent_vacancy
ENV PYTHONUNBUFFERED=1 TV_OUTPUT_DIR=/data
VOLUME /data
ENTRYPOINT ["python", "-m", "transparent_vacancy"]
CMD ["schedule", "--run-on-start"]
