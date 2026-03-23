GHCR_IMAGE=ghcr.io/festusndiritu/letta-backend

.PHONY: build push release

build:
	podman build -t $(GHCR_IMAGE):latest .

push:
	podman push $(GHCR_IMAGE):latest

release: build push
	@echo "Pushed $(GHCR_IMAGE):latest — trigger redeploy in Dokploy"

# Tag a specific version alongside latest
tag:
	@read -p "Version tag (e.g. 0.1.0): " v; \
	docker tag $(GHCR_IMAGE):latest $(GHCR_IMAGE):$$v && \
	docker push $(GHCR_IMAGE):$$v