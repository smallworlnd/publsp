import nox


@nox.session(python=["3.10", "3.11", "3.12", "3.13"])
def tests(session):
    session.install("poetry")
    session.run("poetry", "install", "--no-interaction", "--no-root")
    session.run("pytest")
