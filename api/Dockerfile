FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# --reload makes the read-only bind mount of ./api/app actually mean something:
# a `git pull` on the host is picked up by the running container instead of
# sitting there invisibly until someone restarts it. Without it the mount buys
# nothing over a rebuild, and a deploy that looks successful can leave the old
# code serving.
#
# --reload-dir confines the watcher to the mounted source. Left unscoped,
# uvicorn also watches site-packages, which is a large tree that never changes
# here. watchfiles is already present via uvicorn[standard]; no new dependency.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/srv/app"]
