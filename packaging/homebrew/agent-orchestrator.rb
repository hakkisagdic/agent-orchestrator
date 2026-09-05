# Homebrew formula. Copy into a tap (homebrew-<name>/Formula/) and update `url`
# and `sha256` for each release — see packaging/RELEASING.md.
#
# No `resource` blocks because there are no dependencies, which is the point: the
# virtualenv holds one package and nothing can drift underneath it.
class AgentOrchestrator < Formula
  include Language::Python::Virtualenv

  desc "Attach to the coding agent you are already running: watch, restart, verify, gate"
  homepage "https://hakkisagdic.github.io/agent-orchestrator/"
  url "https://github.com/hakkisagdic/agent-orchestrator/archive/refs/tags/v0.2.1.tar.gz"
  sha256 "96b19c0c161c5988c792e629c322b2b904fbfa87608c0e083cbc2fdd21a44d1a"
  license "MIT"
  head "https://github.com/hakkisagdic/agent-orchestrator.git", branch: "main"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    # `adapters` reads packaged data, so it fails loudly if the JSON files did not
    # ship — the one packaging mistake that a plain `--help` would not catch.
    assert_match "kiro", shell_output("#{bin}/ao adapters")
    assert_match "usage", shell_output("#{bin}/ao lock")
  end
end
