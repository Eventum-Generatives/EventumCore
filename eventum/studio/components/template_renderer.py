import os
from datetime import datetime

import streamlit as st
import yaml
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError
from streamlit_elements import editor, elements  # type: ignore

import eventum.core.models.application_config as models
from eventum.core.plugins.event.base import (EventPluginConfigurationError,
                                             EventPluginRuntimeError)
from eventum.core.plugins.event.jinja import (JinjaEventPlugin, State,
                                              SubprocessManager,
                                              SubprocessManagerMock)
from eventum.core.settings import TIMESTAMP_FIELD_NAME, TIMEZONE
from eventum.studio.components.component import BaseComponent
from eventum.studio.notifiers import NotificationLevel, default_notifier
from eventum.utils.fs import write_temporary_file


class TemplateRenderer(BaseComponent):
    """Component for rendering templates."""

    _SHOW_PROPS = {
        'template_content': str,
        'configuration_content': str,
    }

    def _init_state(self) -> None:
        self._session_state['rendering_result'] = ''
        self._session_state['local_vars_state'] = None
        self._session_state['shared_vars_state'] = None
        self._session_state['subprocess_manager'] = SubprocessManagerMock()

        self._session_state['mock_checkbox'] = True

    def _render(self) -> None:
        """Render currently set template content."""

        content = self._props['configuration_content']

        try:
            config_data = yaml.load(content, yaml.Loader)
        except yaml.YAMLError as e:
            default_notifier(
                message=(
                    'Failed to render template due to configuration '
                    f'parse failure: {e}'
                ),
                level=NotificationLevel.ERROR
            )
            return

        template_path = write_temporary_file(self._props['template_content'])
        base_dir, template_name = os.path.split(template_path)

        try:
            config = models.JinjaEventConfig(
                mode=models.TemplatePickingMode.ALL,
                templates={
                    'template': models.TemplateConfig(template=template_name)
                },
                **config_data
            )
        except (TypeError, ValidationError) as e:
            default_notifier(
                message=(
                    'Failed to render template due to invalid '
                    f'configuration: {e}'
                ),
                level=NotificationLevel.ERROR
            )
            os.remove(template_path)
            return

        timestamp = datetime.now().astimezone(
            tz=TIMEZONE
        ).replace(
            tzinfo=None
        ).isoformat()
        params = {TIMESTAMP_FIELD_NAME: timestamp}

        local_vars: dict | None = self._session_state['local_vars_state']
        shared_vars: State | None = self._session_state['shared_vars_state']
        subprocess_manager = self._session_state['subprocess_manager']

        try:
            plugin = JinjaEventPlugin(
                config=config,
                environment=Environment(
                    loader=FileSystemLoader(searchpath=base_dir),
                    autoescape=False
                )
            )

            if local_vars:
                _, state = local_vars.popitem()
                plugin.local_vars = {template_name: state}

            if shared_vars:
                plugin.shared_vars = shared_vars

            if subprocess_manager:
                plugin.subprocess_manager = subprocess_manager

            result = plugin.render(**params)
        except (EventPluginConfigurationError, EventPluginRuntimeError) as e:
            default_notifier(
                message=(f'Failed to render template: {e}'),
                level=NotificationLevel.ERROR
            )
            return
        finally:
            os.remove(template_path)

        self._session_state['rendering_result'] = result.pop()

        self._session_state['local_vars_state'] = plugin.local_vars
        self._session_state['shared_vars_state'] = plugin.shared_vars
        self._session_state['subprocess_manager'] = plugin.subprocess_manager

        default_notifier(
            message=('Rendered successfully'),
            level=NotificationLevel.SUCCESS
        )

    def _show(self) -> None:
        st.caption(
            'Template rendering',
            help='Press render button and see the result in right side'
        )

        with elements(self._wk('template_renderer')):
            editor.MonacoDiff(
                theme='vs-dark',
                language='javascript',
                original=self._props['template_content'],
                modified=self._session_state['rendering_result'],
                options={
                    'readOnly': True,
                    'cursorSmoothCaretAnimation': True
                },
                height=560,
            )

        col1, col2 = st.columns([3, 1])
        col2.button(
            'Render',
            use_container_width=True,
            type='primary',
            on_click=self._render
        )
        col1.checkbox(
            'Mock subprocesses',
            key=self._wk('mock_checkbox'),
            on_change=(
                lambda:
                self._session_state.__setitem__(
                    'subprocess_manager',
                    SubprocessManager()
                    if not self._session_state['mock_checkbox']
                    else SubprocessManagerMock()
                )
            ),
            help='Mock performing subprocesses in template'
        )

    def clear_state(self) -> None:
        """Clear state of locals, shared and history of subprocess
        commands.
        """
        self._session_state['local_vars_state'] = None
        self._session_state['shared_vars_state'] = None
        self._session_state['subprocess_manager'] = (
            SubprocessManager()
            if not self._session_state['mock_checkbox']
            else SubprocessManagerMock()
        )

    @property
    def local_vars_state(self) -> dict:
        """Get state of template local variables."""
        locals: dict[str, State] = self._session_state['local_vars_state']

        if locals is None:
            return {}

        if locals:
            for value in locals.values():
                return value.as_dict()
        else:
            return {}

    @property
    def shared_vars_state(self) -> dict:
        """Get state of template shared variables."""
        shared: State = self._session_state['shared_vars_state']

        if shared is None:
            return {}

        return shared.as_dict()

    @property
    def subprocess_commands_history(self) -> tuple[tuple[int, str]]:
        """Get history of commands running in templates via `subprocess`."""
        subprocess_manager = self._session_state['subprocess_manager']
        return subprocess_manager.commands_history      # type: ignore
