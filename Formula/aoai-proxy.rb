class AoaiProxy < Formula
  include Language::Python::Virtualenv

  desc "OpenAI-compatible proxy for Azure OpenAI using Entra ID authentication"
  homepage "https://github.com/rioriost/aoai_proxy"
  url "https://github.com/rioriost/aoai_proxy/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "b221f98791377509aaa30ddfff2f05b3650bdf36bf8bc1089095cd523363241a"
  license "MIT"
  head "https://github.com/rioriost/aoai_proxy.git", branch: "main"

  depends_on "azure-cli"
  depends_on "python@3.12"

  resource "annotated-doc" do
    url "https://files.pythonhosted.org/packages/source/a/annotated-doc/annotated_doc-0.0.4.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "annotated-types" do
    url "https://files.pythonhosted.org/packages/source/a/annotated-types/annotated_types-0.7.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/source/a/anyio/anyio-4.12.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "azure-core" do
    url "https://files.pythonhosted.org/packages/source/a/azure-core/azure_core-1.38.3.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "azure-identity" do
    url "https://files.pythonhosted.org/packages/source/a/azure-identity/azure_identity-1.25.3.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/source/c/certifi/certifi-2026.2.25.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "cffi" do
    url "https://files.pythonhosted.org/packages/source/c/cffi/cffi-2.0.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "charset-normalizer" do
    url "https://files.pythonhosted.org/packages/source/c/charset-normalizer/charset_normalizer-3.4.6.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/source/c/click/click-8.3.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "cryptography" do
    url "https://files.pythonhosted.org/packages/source/c/cryptography/cryptography-46.0.5.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "fastapi" do
    url "https://files.pythonhosted.org/packages/source/f/fastapi/fastapi-0.135.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "h11" do
    url "https://files.pythonhosted.org/packages/source/h/h11/h11-0.16.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "httpcore" do
    url "https://files.pythonhosted.org/packages/source/h/httpcore/httpcore-1.0.9.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "httptools" do
    url "https://files.pythonhosted.org/packages/source/h/httptools/httptools-0.7.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/source/h/httpx/httpx-0.28.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/source/i/idna/idna-3.11.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "msal" do
    url "https://files.pythonhosted.org/packages/source/m/msal/msal-1.35.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "msal-extensions" do
    url "https://files.pythonhosted.org/packages/source/m/msal-extensions/msal_extensions-1.3.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pycparser" do
    url "https://files.pythonhosted.org/packages/source/p/pycparser/pycparser-3.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/source/p/pydantic/pydantic-2.12.5.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pydantic-core" do
    url "https://files.pythonhosted.org/packages/source/p/pydantic-core/pydantic_core-2.41.5.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pydantic-settings" do
    url "https://files.pythonhosted.org/packages/source/p/pydantic-settings/pydantic_settings-2.13.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pyjwt" do
    url "https://files.pythonhosted.org/packages/source/P/PyJWT/PyJWT-2.12.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "python-dotenv" do
    url "https://files.pythonhosted.org/packages/source/p/python-dotenv/python_dotenv-1.2.2.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/source/P/PyYAML/PyYAML-6.0.3.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/source/r/requests/requests-2.32.5.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "starlette" do
    url "https://files.pythonhosted.org/packages/source/s/starlette/starlette-0.52.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/source/t/typing_extensions/typing_extensions-4.15.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "typing-inspection" do
    url "https://files.pythonhosted.org/packages/source/t/typing-inspection/typing_inspection-0.4.2.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "urllib3" do
    url "https://files.pythonhosted.org/packages/source/u/urllib3/urllib3-2.6.3.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/source/u/uvicorn/uvicorn-0.42.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "uvloop" do
    url "https://files.pythonhosted.org/packages/source/u/uvloop/uvloop-0.22.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "watchfiles" do
    url "https://files.pythonhosted.org/packages/source/w/watchfiles/watchfiles-1.1.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "websockets" do
    url "https://files.pythonhosted.org/packages/source/w/websockets/websockets-16.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    output = shell_output("#{bin}/aoai-proxy --help 2>&1", 1)
    assert_match "AOAI_PROXY_AZURE_OPENAI_ENDPOINT", output
  end
end
