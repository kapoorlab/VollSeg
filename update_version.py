import subprocess


def update_version_file():
    # Get the latest Git tag
    tag = (
        subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"])
        .decode()
        .strip()
    )
    tag = tag.lstrip("v")
    # Update _version.py
    with open("src/vollseg/_version.py", "w") as version_file:
        version_file.write(f'__version__ = "{tag}"\n')


if __name__ == "__main__":
    update_version_file()
