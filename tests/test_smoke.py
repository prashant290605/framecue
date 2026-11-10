import promolens


def test_package_imports_with_installed_version():
    assert promolens.__version__ == "0.0.1"
