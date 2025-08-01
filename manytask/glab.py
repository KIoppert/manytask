from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import gitlab
import gitlab.const
import gitlab.v4.objects
import requests

from .abstract import RmsApi, Student

logger = logging.getLogger(__name__)


class GitLabApiException(Exception):
    pass


@dataclass
class GitLabConfig:
    """Configuration for GitLab API connection and course settings."""

    base_url: str
    admin_token: str
    dry_run: bool = False


class GitLabApi(RmsApi):
    def __init__(
        self,
        config: GitLabConfig,
    ):
        """Initialize GitLab API client with configuration.

        :param config: GitLabConfig instance containing all necessary settings
        """
        self.dry_run = config.dry_run

        self._base_url = config.base_url
        self._gitlab = gitlab.Gitlab(self.base_url, private_token=config.admin_token)

    def register_new_user(
        self,
        username: str,
        firstname: str,
        lastname: str,
        email: str,
        password: str,
    ) -> None:
        logger.info(f"Creating user (username={username})")
        # was invented to distinguish between different groups of users automatically by secret
        new_user = self._gitlab.users.create(
            {
                "email": email,
                "username": username,
                "name": f"{firstname} {lastname}",
                "external": False,
                "password": password,
                "skip_confirmation": True,
            }
        )
        logger.info(f"Gitlab user created {new_user}")

    def _get_group_by_name(self, group_name: str) -> gitlab.v4.objects.Group:
        short_group_name = group_name.split("/")[-1]
        group_name_with_spaces = " / ".join(group_name.split("/"))

        try:
            return next(
                group
                for group in self._gitlab.groups.list(get_all=True, search=group_name)
                if group.name == short_group_name and group.full_name == group_name_with_spaces
            )
        except StopIteration:
            raise RuntimeError(f"Unable to find group {group_name}")

    def _get_project_by_name(self, project_name: str) -> gitlab.v4.objects.Project:
        short_project_name = project_name.split("/")[-1]

        try:
            return next(
                project
                for project in self._gitlab.projects.list(get_all=True, search=short_project_name)
                if project.path_with_namespace == project_name
            )
        except StopIteration:
            raise RuntimeError(f"Unable to find project {project_name}")

    def create_public_repo(
        self,
        course_group: str,
        course_public_repo: str,
    ) -> None:
        group = self._get_group_by_name(course_group)

        for project in self._gitlab.projects.list(get_all=True, search=course_public_repo):
            if project.path_with_namespace == course_public_repo:
                logger.info(f"Project {course_public_repo} already exists")
                return

        self._gitlab.projects.create(
            {
                "name": course_public_repo,
                "path": course_public_repo,
                "namespace_id": group.id,
                "visibility": "public",
                "shared_runners_enabled": True,
                "auto_devops_enabled": False,
                "initialize_with_readme": True,
            }
        )

    def create_students_group(
        self,
        course_students_group: str,
    ) -> None:
        for group in self._gitlab.groups.list(get_all=True, search=course_students_group):
            if group.name == course_students_group and group.full_name == course_students_group:
                logger.info(f"Group {course_students_group} already exists")
                return

        self._gitlab.groups.create(
            {
                "name": course_students_group,
                "path": course_students_group,
                "visibility": "private",
                "lfs_enabled": True,
                "shared_runners_enabled": True,
            }
        )

    def check_project_exists(
        self,
        username: str,
        course_students_group: str,
    ) -> bool:
        gitlab_project_path = f"{course_students_group}/{username}"
        logger.info(f"Gitlab project path: {gitlab_project_path}")

        for project in self._gitlab.projects.list(get_all=True, search=username):
            logger.info(f"Check project path: {project.path_with_namespace}")

            # Because of implicit conversion
            # TODO: make global problem solve
            if project.path_with_namespace == gitlab_project_path:
                logger.info(f"Project {username} for group {course_students_group} exists")
                return True

        logger.info(f"Project {username} for group {course_students_group} does not exist")
        return False

    def create_project(
        self,
        student: Student,
        course_students_group: str,
        course_public_repo: str,
    ) -> None:
        course_group = self._get_group_by_name(course_students_group)

        gitlab_project_path = f"{course_students_group}/{student.username}"
        logger.info(f"Gitlab project path: {gitlab_project_path}")

        for project in self._gitlab.projects.list(get_all=True, search=student.username):
            logger.info(f"Check project path: {project.path_with_namespace}")

            # Because of implicit conversion
            # TODO: make global problem solve
            if project.path_with_namespace == gitlab_project_path:
                logger.info(f"Project {student.username} for group {course_students_group} already exists")
                project = self._gitlab.projects.get(project.id)

                # ensure student is a member of the project
                try:
                    member = project.members.create(
                        {
                            "user_id": student.id,
                            "access_level": gitlab.const.AccessLevel.DEVELOPER,
                        }
                    )
                    logger.info(f"Project exists, Access to fork granted for {member.username}")
                except gitlab.GitlabCreateError:
                    logger.info(f"Project exists, Access already granted for {student.username} or WTF")

                return

        logger.info(f"Student username {student.username}")
        logger.info(f"Course group {course_group.name}")

        course_public_project = self._get_project_by_name(course_public_repo)
        fork = course_public_project.forks.create(
            {
                "name": student.username,
                "path": student.username,
                "namespace_id": course_group.id,
                "forking_access_level": "disabled",
                # MR target self main
                "mr_default_target_self": True,
                # Enable shared runners
                # TODO: Relay on groups runners
                # "shared_runners_enabled": course_group.shared_runners_setting == "enabled",
                # Set external gitlab-ci config from public repo
                "ci_config_path": f".gitlab-ci.yml@{course_public_project.path_with_namespace}",
                # Merge method to squash
                "merge_method": "squash",
                # Disable AutoDevOps
                "auto_devops_enabled": False,
            }
        )
        project = self._gitlab.projects.get(fork.id)
        # TODO: think .evn config value
        # Unprotect all branches
        for protected_branch in project.protectedbranches.list(get_all=True):
            protected_branch.delete()
        project.save()

        logger.info(f"Git project forked {course_public_project.path_with_namespace} -> {project.path_with_namespace}")

        try:
            member = project.members.create(
                {
                    "user_id": student.id,
                    "access_level": gitlab.const.AccessLevel.DEVELOPER,
                }
            )
            logger.info(f"Access to fork granted for {member.username}")
        except gitlab.GitlabCreateError:
            logger.info(f"Access already granted for {student.username} or smth happened")

    def _parse_user_to_student(
        self,
        user: dict[str, Any],
    ) -> Student:
        return Student(
            id=user["id"],
            username=user["username"],
            name=user["name"],
        )

    def get_students_by_username(
        self,
        username: str,
    ) -> list[Student]:
        users = self._gitlab.users.list(get_all=True, username=username)
        return [self._parse_user_to_student(user._attrs) for user in users]

    def get_student(
        self,
        user_id: int,
    ) -> Student:
        logger.info(f"Searching user {user_id}...")
        user = self._gitlab.users.get(user_id)
        logger.info(f'User found: "{user.username}"')
        return self._parse_user_to_student(user._attrs)

    def get_student_by_username(
        self,
        username: str,
    ) -> Student:
        potential_students = self.get_students_by_username(username)
        potential_students = [student for student in potential_students if student.username == username]
        if len(potential_students) == 0:
            raise GitLabApiException(f"No students found for username {username}")

        student = potential_students[0]
        logger.info(f'User found: "{student.username}"')
        return student

    def check_authenticated_student(
        self,
        oauth_token: str,
    ) -> None:
        headers = {"Authorization": "Bearer " + oauth_token}
        response = requests.get(f"{self.base_url}/api/v4/user", headers=headers)
        response.raise_for_status()

    def get_authenticated_student(
        self,
        oauth_token: str,
    ) -> Student:
        headers = {"Authorization": "Bearer " + oauth_token}
        response = requests.get(f"{self.base_url}/api/v4/user", headers=headers)
        response.raise_for_status()
        return self._parse_user_to_student(response.json())

    def get_url_for_task_base(self, course_public_repo: str, default_branch: str) -> str:
        return f"{self.base_url}/{course_public_repo}/blob/{default_branch}"

    def get_url_for_repo(
        self,
        username: str,
        course_students_group: str,
    ) -> str:
        return f"{self.base_url}/{course_students_group}/{username}"
