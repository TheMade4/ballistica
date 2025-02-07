# Released under the MIT License. See LICENSE for details.
#
"""Functionality related to the high level state of the app."""
from __future__ import annotations

import os
import logging
from enum import Enum
from typing import TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor
from functools import cached_property

from efro.call import tpartial

import _babase
from babase._language import LanguageSubsystem
from babase._plugin import PluginSubsystem
from babase._meta import MetadataSubsystem
from babase._net import NetworkSubsystem
from babase._workspace import WorkspaceSubsystem
from babase._appcomponent import AppComponentSubsystem
from babase._appmodeselector import AppModeSelector
from babase._appintent import AppIntentDefault, AppIntentExec
from babase._stringedit import StringEditSubsystem
from babase._devconsole import DevConsoleSubsystem

if TYPE_CHECKING:
    import asyncio
    from typing import Any, Callable, Coroutine
    from concurrent.futures import Future

    import babase
    from babase import AppIntent, AppMode, AppSubsystem
    from babase._apputils import AppHealthMonitor

    # __FEATURESET_APP_SUBSYSTEM_IMPORTS_BEGIN__
    # This section generated by batools.appmodule; do not edit.

    from baclassic import ClassicSubsystem
    from baplus import PlusSubsystem
    from bauiv1 import UIV1Subsystem

    # __FEATURESET_APP_SUBSYSTEM_IMPORTS_END__


class App:
    """A class for high level app functionality and state.

    Category: **App Classes**

    Use babase.app to access the single shared instance of this class.

    Note that properties not documented here should be considered internal
    and subject to change without warning.
    """

    # pylint: disable=too-many-public-methods

    # A few things defined as non-optional values but not actually
    # available until the app starts.
    plugins: PluginSubsystem
    lang: LanguageSubsystem
    health_monitor: AppHealthMonitor

    # How long we allow shutdown tasks to run before killing them.
    # Currently the entire app hard-exits if shutdown takes 10 seconds,
    # so we need to keep it under that.
    SHUTDOWN_TASK_TIMEOUT_SECONDS = 5

    class State(Enum):
        """High level state the app can be in."""

        # The app has not yet begun starting and should not be used in
        # any way.
        NOT_STARTED = 0

        # The native layer is spinning up its machinery (screens,
        # renderers, etc.). Nothing should happen in the Python layer
        # until this completes.
        NATIVE_BOOTSTRAPPING = 1

        # Python app subsystems are being inited but should not yet
        # interact or do any work.
        INITING = 2

        # Python app subsystems are inited and interacting, but the app
        # has not yet embarked on a high level course of action. It is
        # doing initial account logins, workspace & asset downloads,
        # etc.
        LOADING = 3

        # All pieces are in place and the app is now doing its thing.
        RUNNING = 4

        # Used on platforms such as mobile where the app basically needs
        # to shut down while backgrounded. In this state, all event
        # loops are suspended and all graphics and audio must cease
        # completely. Be aware that the suspended state can be entered
        # from any other state including NATIVE_BOOTSTRAPPING and
        # SHUTTING_DOWN.
        SUSPENDED = 5

        # The app is shutting down. This process may involve sending
        # network messages or other things that can take up to a few
        # seconds, so ideally graphics and audio should remain
        # functional (with fades or spinners or whatever to show
        # something is happening).
        SHUTTING_DOWN = 6

        # The app has completed shutdown. Any code running here should
        # be basically immediate.
        SHUTDOWN_COMPLETE = 7

    class DefaultAppModeSelector(AppModeSelector):
        """Decides which AppModes to use to handle AppIntents.

        This default version is generated by the project updater based
        on the 'default_app_modes' value in the projectconfig.

        It is also possible to modify app mode selection behavior by
        setting app.mode_selector to an instance of a custom
        AppModeSelector subclass. This is a good way to go if you are
        modifying app behavior dynamically via a plugin instead of
        statically in a spinoff project.
        """

        def app_mode_for_intent(
            self, intent: AppIntent
        ) -> type[AppMode] | None:
            # pylint: disable=cyclic-import

            # __DEFAULT_APP_MODE_SELECTION_BEGIN__
            # This section generated by batools.appmodule; do not edit.

            # Ask our default app modes to handle it.
            # (generated from 'default_app_modes' in projectconfig).
            import bascenev1
            import babase

            for appmode in [
                bascenev1.SceneV1AppMode,
                babase.EmptyAppMode,
            ]:
                if appmode.can_handle_intent(intent):
                    return appmode

            return None

            # __DEFAULT_APP_MODE_SELECTION_END__

    def __init__(self) -> None:
        """(internal)

        Do not instantiate this class. You can access the single shared
        instance of it through various high level packages: 'babase.app',
        'bascenev1.app', 'bauiv1.app', etc.
        """

        # Hack for docs-generation: we can be imported with dummy modules
        # instead of our actual binary ones, but we don't function.
        if os.environ.get('BA_RUNNING_WITH_DUMMY_MODULES') == '1':
            return

        self.env: babase.Env = _babase.Env()
        self.state = self.State.NOT_STARTED

        # Default executor which can be used for misc background
        # processing. It should also be passed to any additional asyncio
        # loops we create so that everything shares the same single set
        # of worker threads.
        self.threadpool = ThreadPoolExecutor(
            thread_name_prefix='baworker',
            initializer=self._thread_pool_thread_init,
        )

        self.meta = MetadataSubsystem()
        self.net = NetworkSubsystem()
        self.workspaces = WorkspaceSubsystem()
        self.components = AppComponentSubsystem()
        self.stringedit = StringEditSubsystem()
        self.devconsole = DevConsoleSubsystem()

        # This is incremented any time the app is backgrounded or
        # foregrounded; can be a simple way to determine if network data
        # should be refreshed/etc.
        self.fg_state = 0

        self._subsystems: list[AppSubsystem] = []
        self._native_bootstrapping_completed = False
        self._init_completed = False
        self._meta_scan_completed = False
        self._native_start_called = False
        self._native_suspended = False
        self._native_shutdown_called = False
        self._native_shutdown_complete_called = False
        self._initial_sign_in_completed = False
        self._called_on_initing = False
        self._called_on_loading = False
        self._called_on_running = False
        self._subsystem_registration_ended = False
        self._pending_apply_app_config = False
        self._aioloop: asyncio.AbstractEventLoop | None = None
        self._asyncio_timer: babase.AppTimer | None = None
        self._config: babase.AppConfig | None = None
        self._pending_intent: AppIntent | None = None
        self._intent: AppIntent | None = None
        self._mode: AppMode | None = None
        self._mode_selector: babase.AppModeSelector | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_tasks: list[Coroutine[None, None, None]] = [
            self._wait_for_shutdown_suppressions(),
            self._fade_and_shutdown_graphics(),
            self._fade_and_shutdown_audio(),
        ]
        self._pool_thread_count = 0

    def postinit(self) -> None:
        """Called after we've been inited and assigned to babase.app.

        Anything that accesses babase.app as part of its init process
        must go here instead of __init__.
        """

        # Hack for docs-generation: we can be imported with dummy modules
        # instead of our actual binary ones, but we don't function.
        if os.environ.get('BA_RUNNING_WITH_DUMMY_MODULES') == '1':
            return

        self.lang = LanguageSubsystem()
        self.plugins = PluginSubsystem()

    @property
    def aioloop(self) -> asyncio.AbstractEventLoop:
        """The logic thread's asyncio event loop.

        This allow async tasks to be run in the logic thread.
        Note that, at this time, the asyncio loop is encapsulated
        and explicitly stepped by the engine's logic thread loop and
        thus things like asyncio.get_running_loop() will not return this
        loop from most places in the logic thread; only from within a
        task explicitly created in this loop.
        """
        assert self._aioloop is not None
        return self._aioloop

    @property
    def config(self) -> babase.AppConfig:
        """The babase.AppConfig instance representing the app's config state."""
        assert self._config is not None
        return self._config

    @property
    def mode_selector(self) -> babase.AppModeSelector:
        """Controls which app-modes are used for handling given intents.

        Plugins can override this to change high level app behavior and
        spinoff projects can change the default implementation for the
        same effect.
        """
        if self._mode_selector is None:
            raise RuntimeError(
                'mode_selector cannot be used until the app reaches'
                ' the running state.'
            )
        return self._mode_selector

    @mode_selector.setter
    def mode_selector(self, selector: babase.AppModeSelector) -> None:
        self._mode_selector = selector

    # __FEATURESET_APP_SUBSYSTEM_PROPERTIES_BEGIN__
    # This section generated by batools.appmodule; do not edit.

    @cached_property
    def classic(self) -> ClassicSubsystem | None:
        """Our classic subsystem (if available)."""
        # pylint: disable=cyclic-import

        try:
            from baclassic import ClassicSubsystem

            return ClassicSubsystem()
        except ImportError:
            return None
        except Exception:
            logging.exception('Error importing baclassic.')
            return None

    @cached_property
    def plus(self) -> PlusSubsystem | None:
        """Our plus subsystem (if available)."""
        # pylint: disable=cyclic-import

        try:
            from baplus import PlusSubsystem

            return PlusSubsystem()
        except ImportError:
            return None
        except Exception:
            logging.exception('Error importing baplus.')
            return None

    @cached_property
    def ui_v1(self) -> UIV1Subsystem:
        """Our ui_v1 subsystem (always available)."""
        # pylint: disable=cyclic-import

        from bauiv1 import UIV1Subsystem

        return UIV1Subsystem()

    # __FEATURESET_APP_SUBSYSTEM_PROPERTIES_END__

    def register_subsystem(self, subsystem: AppSubsystem) -> None:
        """Called by the AppSubsystem class. Do not use directly."""

        # We only allow registering new subsystems if we've not yet
        # reached the 'running' state. This ensures that all subsystems
        # receive a consistent set of callbacks starting with
        # on_app_running().
        if self._subsystem_registration_ended:
            raise RuntimeError(
                'Subsystems can no longer be registered at this point.'
            )
        self._subsystems.append(subsystem)

    def add_shutdown_task(self, coro: Coroutine[None, None, None]) -> None:
        """Add a task to be run on app shutdown.

        Note that shutdown tasks will be canceled after
        App.SHUTDOWN_TASK_TIMEOUT_SECONDS if they are still running.
        """
        if (
            self.state is self.State.SHUTTING_DOWN
            or self.state is self.State.SHUTDOWN_COMPLETE
        ):
            stname = self.state.name
            raise RuntimeError(
                f'Cannot add shutdown tasks with current state {stname}.'
            )
        self._shutdown_tasks.append(coro)

    def run(self) -> None:
        """Run the app to completion.

        Note that this only works on builds where Ballistica manages
        its own event loop.
        """
        _babase.run_app()

    def threadpool_submit_no_wait(self, call: Callable[[], Any]) -> None:
        """Submit a call to the app threadpool where result is not needed.

        Normally, doing work in a thread-pool involves creating a future
        and waiting for its result, which is an important step because it
        propagates any Exceptions raised by the submitted work. When the
        result in not important, however, this call can be used. The app
        will log any exceptions that occur.
        """
        fut = self.threadpool.submit(call)
        fut.add_done_callback(self._threadpool_no_wait_done)

    def set_intent(self, intent: AppIntent) -> None:
        """Set the intent for the app.

        Intent defines what the app is trying to do at a given time.
        This call is asynchronous; the intent switch will happen in the
        logic thread in the near future. If set_intent is called
        repeatedly before the change takes place, the final intent to be
        set will be used.
        """

        # Mark this one as pending. We do this synchronously so that the
        # last one marked actually takes effect if there is overlap
        # (doing this in the bg thread could result in race conditions).
        self._pending_intent = intent

        # Do the actual work of calcing our app-mode/etc. in a bg thread
        # since it may block for a moment to load modules/etc.
        self.threadpool_submit_no_wait(tpartial(self._set_intent, intent))

    def push_apply_app_config(self) -> None:
        """Internal. Use app.config.apply() to apply app config changes."""
        # To be safe, let's run this by itself in the event loop.
        # This avoids potential trouble if this gets called mid-draw or
        # something like that.
        self._pending_apply_app_config = True
        _babase.pushcall(self._apply_app_config, raw=True)

    def on_native_start(self) -> None:
        """Called by the native layer when the app is being started."""
        assert _babase.in_logic_thread()
        assert not self._native_start_called
        self._native_start_called = True
        self._update_state()

    def on_native_bootstrapping_complete(self) -> None:
        """Called by the native layer once its ready to rock."""
        assert _babase.in_logic_thread()
        assert not self._native_bootstrapping_completed
        self._native_bootstrapping_completed = True
        self._update_state()

    def on_native_suspend(self) -> None:
        """Called by the native layer when the app is suspended."""
        assert _babase.in_logic_thread()
        assert not self._native_suspended  # Should avoid redundant calls.
        self._native_suspended = True
        self._update_state()

    def on_native_unsuspend(self) -> None:
        """Called by the native layer when the app suspension ends."""
        assert _babase.in_logic_thread()
        assert self._native_suspended  # Should avoid redundant calls.
        self._native_suspended = False
        self._update_state()

    def on_native_shutdown(self) -> None:
        """Called by the native layer when the app starts shutting down."""
        assert _babase.in_logic_thread()
        self._native_shutdown_called = True
        self._update_state()

    def on_native_shutdown_complete(self) -> None:
        """Called by the native layer when the app is done shutting down."""
        assert _babase.in_logic_thread()
        self._native_shutdown_complete_called = True
        self._update_state()

    def read_config(self) -> None:
        """(internal)"""
        from babase._appconfig import read_app_config

        self._config = read_app_config()

    def handle_deep_link(self, url: str) -> None:
        """Handle a deep link URL."""
        from babase._language import Lstr

        assert _babase.in_logic_thread()

        appname = _babase.appname()
        if url.startswith(f'{appname}://code/'):
            code = url.replace(f'{appname}://code/', '')
            if self.classic is not None:
                self.classic.accounts.add_pending_promo_code(code)
        else:
            try:
                _babase.screenmessage(
                    Lstr(resource='errorText'), color=(1, 0, 0)
                )
                _babase.getsimplesound('error').play()
            except ImportError:
                pass

    def on_initial_sign_in_complete(self) -> None:
        """Called when initial sign-in (or lack thereof) completes.

        This normally gets called by the plus subsystem. The
        initial-sign-in process may include tasks such as syncing
        account workspaces or other data so it may take a substantial
        amount of time.
        """
        assert _babase.in_logic_thread()
        assert not self._initial_sign_in_completed

        # Tell meta it can start scanning extra stuff that just showed
        # up (namely account workspaces).
        self.meta.start_extra_scan()

        self._initial_sign_in_completed = True
        self._update_state()

    def _set_intent(self, intent: AppIntent) -> None:
        # This should be happening in a bg thread.
        assert not _babase.in_logic_thread()
        try:
            # Ask the selector what app-mode to use for this intent.
            if self.mode_selector is None:
                raise RuntimeError('No AppModeSelector set.')
            modetype = self.mode_selector.app_mode_for_intent(intent)

            # NOTE: Since intents are somewhat high level things, should
            # we do some universal thing like a screenmessage saying
            # 'The app cannot handle that request' on failure?

            if modetype is None:
                raise RuntimeError(
                    f'No app-mode found to handle app-intent'
                    f' type {type(intent)}.'
                )

            # Make sure the app-mode the selector gave us *actually*
            # supports the intent.
            if not modetype.can_handle_intent(intent):
                raise RuntimeError(
                    f'Intent {intent} cannot be handled by AppMode type'
                    f' {modetype} (selector {self.mode_selector}'
                    f' incorrectly thinks that it can be).'
                )

            # Ok; seems legit. Now instantiate the mode if necessary and
            # kick back to the logic thread to apply.
            mode = modetype()
            _babase.pushcall(
                tpartial(self._apply_intent, intent, mode),
                from_other_thread=True,
            )
        except Exception:
            logging.exception('Error setting app intent to %s.', intent)
            _babase.pushcall(
                tpartial(self._display_set_intent_error, intent),
                from_other_thread=True,
            )

    def _apply_intent(self, intent: AppIntent, mode: AppMode) -> None:
        assert _babase.in_logic_thread()

        # ONLY apply this intent if it is still the most recent one
        # submitted.
        if intent is not self._pending_intent:
            return

        # If the app-mode for this intent is different than the active
        # one, switch.
        if type(mode) is not type(self._mode):
            if self._mode is None:
                is_initial_mode = True
            else:
                is_initial_mode = False
                try:
                    self._mode.on_deactivate()
                except Exception:
                    logging.exception(
                        'Error deactivating app-mode %s.', self._mode
                    )
            self._mode = mode
            try:
                mode.on_activate()
            except Exception:
                # Hmm; what should we do in this case?...
                logging.exception('Error activating app-mode %s.', mode)

            # Let the world know when we first have an app-mode; certain
            # app stuff such as input processing can proceed at that
            # point.
            if is_initial_mode:
                _babase.on_initial_app_mode_set()

        try:
            mode.handle_intent(intent)
        except Exception:
            logging.exception(
                'Error handling intent %s in app-mode %s.', intent, mode
            )

    def _display_set_intent_error(self, intent: AppIntent) -> None:
        """Show the *user* something went wrong setting an intent."""
        from babase._language import Lstr

        del intent
        _babase.screenmessage(Lstr(resource='errorText'), color=(1, 0, 0))
        _babase.getsimplesound('error').play()

    def _on_initing(self) -> None:
        """Called when the app enters the initing state.

        Here we can put together subsystems and other pieces for the
        app, but most things should not be doing any work yet.
        """
        # pylint: disable=cyclic-import
        from babase import _asyncio
        from babase import _appconfig
        from babase._apputils import AppHealthMonitor
        from babase import _env

        assert _babase.in_logic_thread()

        _env.on_app_state_initing()

        self._aioloop = _asyncio.setup_asyncio()
        self.health_monitor = AppHealthMonitor()

        # __FEATURESET_APP_SUBSYSTEM_CREATE_BEGIN__
        # This section generated by batools.appmodule; do not edit.

        # Poke these attrs to create all our subsystems.
        _ = self.plus
        _ = self.classic
        _ = self.ui_v1

        # __FEATURESET_APP_SUBSYSTEM_CREATE_END__

        # We're a pretty short-lived state. This should flip us to
        # 'loading'.
        self._init_completed = True
        self._update_state()

    def _on_loading(self) -> None:
        """Called when we enter the loading state.

        At this point, all built-in pieces of the app should be in place
        and can start talking to each other and doing work. Though at a
        high level, the goal of the app at this point is only to sign in
        to initial accounts, download workspaces, and otherwise prepare
        itself to really 'run'.
        """
        assert _babase.in_logic_thread()

        # Get meta-system scanning built-in stuff in the bg.
        self.meta.start_scan(scan_complete_cb=self._on_meta_scan_complete)

        # Inform all app subsystems in the same order they were inited.
        # Operate on a copy here because subsystems can still be added
        # at this point.
        for subsystem in self._subsystems.copy():
            try:
                subsystem.on_app_loading()
            except Exception:
                logging.exception(
                    'Error in on_app_loading for subsystem %s.', subsystem
                )

        # Normally plus tells us when initial sign-in is done. If plus
        # is not present, however, we just do it ourself so we can
        # proceed on to the running state.
        if self.plus is None:
            _babase.pushcall(self.on_initial_sign_in_complete)

    def _on_meta_scan_complete(self) -> None:
        """Called when meta-scan is done doing its thing."""
        assert _babase.in_logic_thread()

        # Now that we know what's out there, build our final plugin set.
        self.plugins.on_meta_scan_complete()

        assert not self._meta_scan_completed
        self._meta_scan_completed = True
        self._update_state()

    def _on_running(self) -> None:
        """Called when we enter the running state.

        At this point, all workspaces, initial accounts, etc. are in place
        and we can actually get started doing whatever we're gonna do.
        """
        assert _babase.in_logic_thread()

        # Let our native layer know.
        _babase.on_app_running()

        # Set a default app-mode-selector if none has been set yet
        # by a plugin or whatnot.
        if self._mode_selector is None:
            self._mode_selector = self.DefaultAppModeSelector()

        # Inform all app subsystems in the same order they were
        # registered. Operate on a copy here because subsystems can
        # still be added at this point.
        #
        # NOTE: Do we need to allow registering still at this point? If
        # something gets registered here, it won't have its
        # on_app_running callback called. Hmm; I suppose that's the only
        # way that plugins can register subsystems though.
        for subsystem in self._subsystems.copy():
            try:
                subsystem.on_app_running()
            except Exception:
                logging.exception(
                    'Error in on_app_running for subsystem %s.', subsystem
                )

        # Cut off new subsystem additions at this point.
        self._subsystem_registration_ended = True

        # If 'exec' code was provided to the app, always kick that off
        # here as an intent.
        exec_cmd = _babase.exec_arg()
        if exec_cmd is not None:
            self.set_intent(AppIntentExec(exec_cmd))
        elif self._pending_intent is None:
            # Otherwise tell the app to do its default thing *only* if a
            # plugin hasn't already told it to do something.
            self.set_intent(AppIntentDefault())

    def _apply_app_config(self) -> None:
        assert _babase.in_logic_thread()

        _babase.lifecyclelog('apply-app-config')

        # If multiple apply calls have been made, only actually apply
        # once.
        if not self._pending_apply_app_config:
            return

        _pending_apply_app_config = False

        # Inform all app subsystems in the same order they were inited.
        # Operate on a copy here because subsystems may still be able to
        # be added at this point.
        for subsystem in self._subsystems.copy():
            try:
                subsystem.do_apply_app_config()
            except Exception:
                logging.exception(
                    'Error in do_apply_app_config for subsystem %s.', subsystem
                )

        # Let the native layer do its thing.
        _babase.do_apply_app_config()

    def _update_state(self) -> None:
        # pylint: disable=too-many-branches
        assert _babase.in_logic_thread()

        # Shutdown-complete trumps absolutely all.
        if self._native_shutdown_complete_called:
            if self.state is not self.State.SHUTDOWN_COMPLETE:
                self.state = self.State.SHUTDOWN_COMPLETE
                _babase.lifecyclelog('app state shutdown complete')
                self._on_shutdown_complete()

        # Shutdown trumps all. Though we can't start shutting down until
        # init is completed since we need our asyncio stuff to exist for
        # the shutdown process.
        elif self._native_shutdown_called and self._init_completed:
            # Entering shutdown state:
            if self.state is not self.State.SHUTTING_DOWN:
                self.state = self.State.SHUTTING_DOWN
                _babase.lifecyclelog('app state shutting down')
                self._on_shutting_down()

        elif self._native_suspended:
            # Entering suspended state:
            if self.state is not self.State.SUSPENDED:
                self.state = self.State.SUSPENDED
                self._on_suspend()
        else:
            # Leaving suspended state:
            if self.state is self.State.SUSPENDED:
                self._on_unsuspend()

            # Entering or returning to running state
            if self._initial_sign_in_completed and self._meta_scan_completed:
                if self.state != self.State.RUNNING:
                    self.state = self.State.RUNNING
                    _babase.lifecyclelog('app state running')
                    if not self._called_on_running:
                        self._called_on_running = True
                        self._on_running()
            # Entering or returning to loading state:
            elif self._init_completed:
                if self.state is not self.State.LOADING:
                    self.state = self.State.LOADING
                    _babase.lifecyclelog('app state loading')
                    if not self._called_on_loading:
                        self._called_on_loading = True
                        self._on_loading()

            # Entering or returning to initing state:
            elif self._native_bootstrapping_completed:
                if self.state is not self.State.INITING:
                    self.state = self.State.INITING
                    _babase.lifecyclelog('app state initing')
                    if not self._called_on_initing:
                        self._called_on_initing = True
                        self._on_initing()

            # Entering or returning to native bootstrapping:
            elif self._native_start_called:
                if self.state is not self.State.NATIVE_BOOTSTRAPPING:
                    self.state = self.State.NATIVE_BOOTSTRAPPING
                    _babase.lifecyclelog('app state native bootstrapping')
            else:
                # Only logical possibility left is NOT_STARTED, in which
                # case we should not be getting called.
                logging.warning(
                    'App._update_state called while in %s state;'
                    ' should not happen.',
                    self.state.value,
                    stack_info=True,
                )

    async def _shutdown(self) -> None:
        import asyncio

        _babase.lock_all_input()
        try:
            async with asyncio.TaskGroup() as task_group:
                for task_coro in self._shutdown_tasks:
                    # Note: Mypy currently complains if we don't take
                    # this return value, but we don't actually need to.
                    # https://github.com/python/mypy/issues/15036
                    _ = task_group.create_task(
                        self._run_shutdown_task(task_coro)
                    )
        except* Exception:
            logging.exception('Unexpected error(s) in shutdown.')

        # Note: ideally we should run this directly here, but currently
        # it does some legacy stuff which blocks, so running it here
        # gives us asyncio task-took-too-long warnings. If we can
        # convert those to nice graceful async tasks we should revert
        # this to a direct call.
        _babase.pushcall(_babase.complete_shutdown)

    async def _run_shutdown_task(
        self, coro: Coroutine[None, None, None]
    ) -> None:
        """Run a shutdown task; report errors and abort if taking too long."""
        import asyncio

        task = asyncio.create_task(coro)
        try:
            await asyncio.wait_for(task, self.SHUTDOWN_TASK_TIMEOUT_SECONDS)
        except Exception:
            logging.exception('Error in shutdown task (%s).', coro)

    def _on_suspend(self) -> None:
        """Called when the app goes to a suspended state."""
        assert _babase.in_logic_thread()

        # Suspend all app subsystems in the opposite order they were inited.
        for subsystem in reversed(self._subsystems):
            try:
                subsystem.on_app_suspend()
            except Exception:
                logging.exception(
                    'Error in on_app_suspend for subsystem %s.', subsystem
                )

    def _on_unsuspend(self) -> None:
        """Called when unsuspending."""
        assert _babase.in_logic_thread()
        self.fg_state += 1

        # Unsuspend all app subsystems in the same order they were inited.
        for subsystem in self._subsystems:
            try:
                subsystem.on_app_unsuspend()
            except Exception:
                logging.exception(
                    'Error in on_app_unsuspend for subsystem %s.', subsystem
                )

    def _on_shutting_down(self) -> None:
        """(internal)"""
        assert _babase.in_logic_thread()

        # Inform app subsystems that we're shutting down in the opposite
        # order they were inited.
        for subsystem in reversed(self._subsystems):
            try:
                subsystem.on_app_shutdown()
            except Exception:
                logging.exception(
                    'Error in on_app_shutdown for subsystem %s.', subsystem
                )

        # Now kick off any async shutdown task(s).
        assert self._aioloop is not None
        self._shutdown_task = self._aioloop.create_task(self._shutdown())

    def _on_shutdown_complete(self) -> None:
        """(internal)"""
        assert _babase.in_logic_thread()

        # Inform app subsystems that we're done shutting down in the opposite
        # order they were inited.
        for subsystem in reversed(self._subsystems):
            try:
                subsystem.on_app_shutdown_complete()
            except Exception:
                logging.exception(
                    'Error in on_app_shutdown_complete for subsystem %s.',
                    subsystem,
                )

    async def _wait_for_shutdown_suppressions(self) -> None:
        import asyncio

        # Spin and wait for anything blocking shutdown to complete.
        starttime = _babase.apptime()
        _babase.lifecyclelog('shutdown-suppress wait begin')
        while _babase.shutdown_suppress_count() > 0:
            await asyncio.sleep(0.001)
        _babase.lifecyclelog('shutdown-suppress wait end')
        duration = _babase.apptime() - starttime
        if duration > 1.0:
            logging.warning(
                'Shutdown-suppressions lasted longer than ideal '
                '(%.2f seconds).',
                duration,
            )

    async def _fade_and_shutdown_graphics(self) -> None:
        import asyncio

        # Kick off a short fade and give it time to complete.
        _babase.lifecyclelog('fade-and-shutdown-graphics begin')
        _babase.fade_screen(False, time=0.15)
        await asyncio.sleep(0.15)

        # Now tell the graphics system to go down and wait until
        # it has done so.
        _babase.graphics_shutdown_begin()
        while not _babase.graphics_shutdown_is_complete():
            await asyncio.sleep(0.01)
        _babase.lifecyclelog('fade-and-shutdown-graphics end')

    async def _fade_and_shutdown_audio(self) -> None:
        import asyncio

        # Tell the audio system to go down and give it a bit of
        # time to do so gracefully.
        _babase.lifecyclelog('fade-and-shutdown-audio begin')
        _babase.audio_shutdown_begin()
        await asyncio.sleep(0.15)
        while not _babase.audio_shutdown_is_complete():
            await asyncio.sleep(0.01)
        _babase.lifecyclelog('fade-and-shutdown-audio end')

    def _threadpool_no_wait_done(self, fut: Future) -> None:
        try:
            fut.result()
        except Exception:
            logging.exception(
                'Error in work submitted via threadpool_submit_no_wait()'
            )

    def _thread_pool_thread_init(self) -> None:
        # Help keep things clear in profiling tools/etc.
        self._pool_thread_count += 1
        _babase.set_thread_name(f'ballistica worker-{self._pool_thread_count}')
