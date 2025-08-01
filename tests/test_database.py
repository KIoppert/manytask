from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from manytask.models import Course, Task, TaskGroup
from manytask.config import ManytaskDeadlinesConfig
from manytask.glab import Student


# ruff: noqa
from tests.test_db_api import (
    db_api,
    first_course_config,
    first_course_deadlines_config,
    second_course_config,
    second_course_deadlines_config,
    db_api_with_two_initialized_courses,
    FIRST_COURSE_NAME,
    SECOND_COURSE_NAME,
)


def create_test_config(tasks_config):
    """Helper function to create a valid config with all required fields"""
    return {
        "version": 1,
        "settings": {
            "timezone": "UTC",
            "course_name": "Test Course",
            "gitlab_base_url": "https://gitlab.test.com",
            "public_repo": "test-repo",
            "students_group": "students",
        },
        "ui": {
            "task_url_template": "https://gitlab.test.com/{course}/{student}/{task}",
        },
        "deadlines": {"timezone": "UTC", "schedule": [tasks_config]},
    }


def create_base_task_config(group_name: str, enabled: bool = True):
    """Helper function to create a base task config with standard fields"""
    return {
        "group": group_name,
        "start": datetime.now(ZoneInfo("UTC")),
        "end": datetime.now(ZoneInfo("UTC")) + timedelta(days=1),
        "enabled": enabled,
    }


def create_task_entry(task_name: str, enabled: bool = True, score: int = 100):
    """Helper function to create a task entry for the config"""
    return {"task": task_name, "enabled": enabled, "score": score}


def setup_course_with_tasks(
    session, course_name: str, tasks_data: list[tuple[str, str, str]]
) -> tuple[Course, list[Task]]:
    """Helper function to set up a course with tasks

    Args:
        session: SQLAlchemy session
        course_name: Name of the course
        tasks_data: List of (task_name, group_name, course_name) tuples

    Returns:
        Tuple of (course, list of created tasks)
    """
    course = session.query(Course).filter_by(name=course_name).one()
    tasks = []
    groups = {}

    for task_name, group_name, _ in tasks_data:
        if group_name not in groups:
            groups[group_name] = TaskGroup(name=group_name, course=course)
            session.add(groups[group_name])

        task = Task(name=task_name, group=groups[group_name])
        tasks.append(task)
        session.add(task)

    session.commit()
    return course, tasks


def test_move_task_between_groups(db_api_with_two_initialized_courses, session):
    """Test moving a task from one group to another"""

    _, tasks = setup_course_with_tasks(session, "Test Course", [("task1", "group1", "Test Course")])

    tasks_config = create_base_task_config("group2")
    tasks_config["tasks"] = [create_task_entry("task1")]

    db_api_with_two_initialized_courses._update_task_groups_from_config(
        FIRST_COURSE_NAME, ManytaskDeadlinesConfig(**create_test_config(tasks_config)["deadlines"])
    )

    task = session.query(Task).filter_by(name="task1").one()
    assert task.group.name == "group2"


def test_create_missing_group(db_api_with_two_initialized_courses, session):
    """Test creating a new group when moving task to non-existent group"""

    _, tasks = setup_course_with_tasks(session, "Test Course", [("task1", "group1", "Test Course")])

    tasks_config = create_base_task_config("new_group")
    tasks_config["tasks"] = [create_task_entry("task1")]

    db_api_with_two_initialized_courses._update_task_groups_from_config(
        FIRST_COURSE_NAME, ManytaskDeadlinesConfig(**create_test_config(tasks_config)["deadlines"])
    )

    task = session.query(Task).filter_by(name="task1").one()
    assert task.group.name == "new_group"
    assert session.query(TaskGroup).filter_by(name="new_group").count() == 1


def test_multiple_courses(db_api_with_two_initialized_courses, session):
    """Test that tasks are only moved in the correct course"""

    tasks_data = [("task1", "group1", "Test Course"), ("task1", "group1", "Another Test Course")]

    _, tasks1 = setup_course_with_tasks(session, "Test Course", [tasks_data[0]])

    _, tasks2 = setup_course_with_tasks(session, "Another Test Course", [tasks_data[1]])

    course1 = session.query(Course).filter_by(name="Test Course").one()
    group2_c1 = TaskGroup(name="group2", course=course1)
    session.add(group2_c1)
    session.commit()

    tasks_config = create_base_task_config("group2")
    tasks_config["tasks"] = [create_task_entry("task1")]

    db_api_with_two_initialized_courses._update_task_groups_from_config(
        FIRST_COURSE_NAME, ManytaskDeadlinesConfig(**create_test_config(tasks_config)["deadlines"])
    )

    task1_c1 = session.query(Task).join(TaskGroup).filter(Task.name == "task1", TaskGroup.course_id == course1.id).one()
    task1_c2 = session.query(Task).join(TaskGroup).filter(Task.name == "task1", TaskGroup.course_id != course1.id).one()

    assert task1_c1.group.name == "group2"
    assert task1_c2.group.name == "group1"


def test_multiple_task_moves(db_api_with_two_initialized_courses, session):
    """Test moving multiple tasks between groups"""

    tasks_data = [
        ("task1", "group1", "Test Course"),
        ("task2", "group2", "Test Course"),
        ("task3", "group2", "Test Course"),
    ]
    _, tasks = setup_course_with_tasks(session, "Test Course", tasks_data)

    course = session.query(Course).filter_by(name="Test Course").one()
    group3 = TaskGroup(name="group3", course=course)
    session.add(group3)
    session.commit()

    tasks_config = create_base_task_config("group3")
    tasks_config["tasks"] = [create_task_entry("task1"), create_task_entry("task2"), create_task_entry("task3")]

    db_api_with_two_initialized_courses._update_task_groups_from_config(
        FIRST_COURSE_NAME, ManytaskDeadlinesConfig(**create_test_config(tasks_config)["deadlines"])
    )

    tasks = session.query(Task).filter(Task.name.in_(["task1", "task2", "task3"])).all()
    for task in tasks:
        assert task.group.name == "group3"


def test_get_courses_names_with_no_courses(db_api):
    assert db_api.get_user_courses_names("some_user") == []
    assert db_api.get_all_courses_names() == []


def test_get_courses_names_with_courses(db_api_with_two_initialized_courses):
    username1 = "username1"
    first_name1 = "Ivan"
    last_name1 = "Ivanov"
    username2 = "username2"
    first_name2 = "John"
    last_name2 = "Smith"
    username3 = "username3"
    first_name3 = "Peter"
    last_name3 = "Nordstrom"

    db_api_with_two_initialized_courses.create_user_if_not_exist(username1, first_name1, last_name1)
    db_api_with_two_initialized_courses.create_user_if_not_exist(username2, first_name2, last_name2)
    db_api_with_two_initialized_courses.create_user_if_not_exist(username3, first_name3, last_name3)

    assert db_api_with_two_initialized_courses.get_user_courses_names(username1) == []
    assert db_api_with_two_initialized_courses.get_user_courses_names(username2) == []
    assert db_api_with_two_initialized_courses.get_user_courses_names(username3) == []
    assert sorted(db_api_with_two_initialized_courses.get_all_courses_names()) == sorted(
        [FIRST_COURSE_NAME, SECOND_COURSE_NAME]
    )

    db_api_with_two_initialized_courses.sync_stored_user(FIRST_COURSE_NAME, username1, "repo1", True)
    db_api_with_two_initialized_courses.sync_stored_user(SECOND_COURSE_NAME, username2, "repo2", False)
    db_api_with_two_initialized_courses.sync_stored_user(FIRST_COURSE_NAME, username3, "repo3", False)
    db_api_with_two_initialized_courses.sync_stored_user(SECOND_COURSE_NAME, username3, "repo3", True)

    assert db_api_with_two_initialized_courses.get_user_courses_names(username1) == [FIRST_COURSE_NAME]
    assert db_api_with_two_initialized_courses.get_user_courses_names(username2) == [SECOND_COURSE_NAME]
    assert sorted(db_api_with_two_initialized_courses.get_user_courses_names(username3)) == sorted(
        [FIRST_COURSE_NAME, SECOND_COURSE_NAME]
    )
