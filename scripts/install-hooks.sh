#!/bin/bash
# Install git hooks for the app-builder repo.

REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "Installing pre-push hook..."
cp "$REPO_ROOT/scripts/pre-push-validate.sh" "$REPO_ROOT/.git/hooks/pre-push"
chmod +x "$REPO_ROOT/.git/hooks/pre-push"
echo "✓ Pre-push hook installed — pushes to main will be validated first."
echo "  (bypass with: git push --no-verify)"
