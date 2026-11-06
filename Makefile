.PHONY: web-install web-dev web-build web-typecheck web-lint web-test check

FRONTEND := frontend

web-install:
	cd $(FRONTEND) && npm ci

web-dev:
	cd $(FRONTEND) && npm run dev

web-build:
	cd $(FRONTEND) && npm run build

web-typecheck:
	cd $(FRONTEND) && npx tsc -b --noEmit

web-lint:
	cd $(FRONTEND) && npm run lint

web-test:
	cd $(FRONTEND) && npm test

check: web-typecheck web-lint web-test

