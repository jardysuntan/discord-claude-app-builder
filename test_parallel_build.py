from helpers.parallel_build import build_parallel_tasks, should_parallelize


def test_should_parallelize_for_explicit_flag():
    assert should_parallelize("Build a finance app parallel:true with login and backend sync")


def test_should_parallelize_for_complex_request():
    desc = "Build an app with authentication, backend database sync, platform-specific iOS and Android integrations, and tests."
    assert should_parallelize(desc)


def test_should_not_parallelize_simple_request():
    assert not should_parallelize("Build a simple timer app with one screen")


def test_build_parallel_tasks_returns_scoped_slices():
    tasks = build_parallel_tasks("NovaApp", "Build a productivity app with auth and subscriptions")
    assert [task.slug for task in tasks] == ["ui", "logic", "platform", "tests"]
    assert all("NovaApp" in task.prompt for task in tasks)
    assert any("Compose" in task.prompt for task in tasks)
