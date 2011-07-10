############################################################################
##
## Copyright (C), all rights reserved:
##      2010 Brett Smith <tanktarta@blueyonder.co.uk>
##
## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License version 2
##
## Configuration Application for Logitech "G" keyboards
##
############################################################################

import pygtk
pygtk.require('2.0')
import gtk
import gobject
import pango
import dbus.service
import os
import sys
import g15globals
import g15profile
import gconf
import g15pluginmanager
import g15driver
import g15desktop
import g15drivermanager
import g15devices
import g15util
import subprocess
import shutil
import logging
import traceback

logger = logging.getLogger("config")

# Determine if appindicator is available, this decides the nature
# of the message displayed when the Gnome15 service is not running
HAS_APPINDICATOR=False
try :
    import appindicator
    appindicator.__path__
    HAS_APPINDICATOR=True
except:
    pass

# Store the temporary profile icons here (for when the icon comes from a window, the filename is not known
icons_dir = os.path.join(os.path.expanduser("~"),".cache", "gnome15", "macro_profiles")
if not os.path.exists(icons_dir):
    os.makedirs(icons_dir)

PALE_RED = gtk.gdk.Color(213, 65, 54)


BUS_NAME="org.gnome15.Configuration"
NAME="/org/gnome15/Config"
IF_NAME="org.gnome15.Config"

STOPPED = 0
STARTING = 1
STARTED = 2
STOPPING = 3 

class G15ConfigService(dbus.service.Object):
    """
    DBUS Service used to prevent g15-config from running more than once. Each run will
    test if this service is available, if it is, then the Present function will be 
    called and the runtime exited.
    """
    
    def __init__(self, bus, window):
        bus_name = dbus.service.BusName(BUS_NAME, bus=bus, replace_existing=False, allow_replacement=False, do_not_queue=True)
        dbus.service.Object.__init__(self, bus_name, NAME)
        self.window = window
        
    @dbus.service.method(IF_NAME, in_signature='', out_signature='')
    def Present(self):
        self.window.present()

class G15Config:
    
    """
    Configuration user interface for Gnome15. Allows selection and configuration
    of the device, macros and enabled plugins.
    """
    
    adjusting = False

    def __init__(self, parent_window=None, service=None):
        self.parent_window = parent_window
        
        self._signal_handles = []
        self.notify_handles = []
        self.control_notify_handles = []
        self.selected_id = None
        self.service = service
        self.conf_client = gconf.client_get_default()
        self.rows = None
        self.adjusting = False
        self.gnome15_service = None
        self.connected = False
        self.color_button = None
        self.screen_services = {}
        self.state = STOPPED
        self.driver = None
        
        # Load main Glade file
        g15Config = os.path.join(g15globals.glade_dir, 'g15-config.glade')        
        self.widget_tree = gtk.Builder()
        self.widget_tree.add_from_file(g15Config)
        self.main_window = self.widget_tree.get_object("MainWindow")
        
        # Make sure there is only one g15config running
        self.session_bus = dbus.SessionBus()
        try :
            G15ConfigService(self.session_bus, self.main_window)
        except dbus.exceptions.NameExistsException as e:
            self.session_bus.get_object(BUS_NAME, NAME).Present()
            self.session_bus.close()
            g15profile.notifier.stop()
            sys.exit()

        # Widgets
        self.site_label = self.widget_tree.get_object("SiteLabel")
        self.cycle_screens = self.widget_tree.get_object("CycleScreens")
        self.cycle_screens_options = self.widget_tree.get_object("CycleScreensOptions")
        self.cycle_seconds = self.widget_tree.get_object("CycleAdjustment")
        self.cycle_seconds_widget = self.widget_tree.get_object("CycleSeconds")
        self.plugin_model = self.widget_tree.get_object("PluginModel")
        self.plugin_tree = self.widget_tree.get_object("PluginTree")
        self.plugin_enabled_renderer = self.widget_tree.get_object("PluginEnabledRenderer")
        self.main_vbox = self.widget_tree.get_object("MainVBox")
        self.profiles_tree = self.widget_tree.get_object("ProfilesTree")
        self.profileNameColumn = self.widget_tree.get_object("ProfileName")
        self.keyNameColumn = self.widget_tree.get_object("KeyName")
        self.macroNameColumn = self.widget_tree.get_object("MacroName")
        self.macro_list = self.widget_tree.get_object("MacroList")
        self.application = self.widget_tree.get_object("ApplicationLocation")
        self.m1 = self.widget_tree.get_object("M1") 
        self.m2 = self.widget_tree.get_object("M2") 
        self.m3 = self.widget_tree.get_object("M3")
        self.window_model = self.widget_tree.get_object("WindowModel")
        self.window_combo = self.widget_tree.get_object("WindowCombo")
        self.remove_button = self.widget_tree.get_object("RemoveButton")
        self.activate_on_focus = self.widget_tree.get_object("ActivateProfileOnFocusCheckbox")
        self.macro_name_renderer = self.widget_tree.get_object("MacroNameRenderer")
        self.profile_name_renderer = self.widget_tree.get_object("ProfileNameRenderer")
        self.window_label = self.widget_tree.get_object("WindowLabel")
        self.activate_by_default = self.widget_tree.get_object("ActivateByDefaultCheckbox")
        self.send_delays = self.widget_tree.get_object("SendDelaysCheckbox")
        self.fixed_delays = self.widget_tree.get_object("FixedDelaysCheckbox")
        self.release_delay = self.widget_tree.get_object("ReleaseDelay")
        self.press_delay = self.widget_tree.get_object("PressDelay")
        self.press_delay_adjustment = self.widget_tree.get_object("PressDelayAdjustment")
        self.release_delay_adjustment = self.widget_tree.get_object("ReleaseDelayAdjustment")
        self.profile_icon = self.widget_tree.get_object("ProfileIcon")
        self.icon_browse_button = self.widget_tree.get_object("BrowseForIcon")
        self.clear_icon_button = self.widget_tree.get_object("ClearIcon")
        self.macro_properties_button = self.widget_tree.get_object("MacroPropertiesButton")
        self.new_macro_button = self.widget_tree.get_object("NewMacroButton")
        self.delete_macro_button = self.widget_tree.get_object("DeleteMacroButton")
        self.memory_bank_label = self.widget_tree.get_object("MemoryBankLabel")
        self.macro_name_field = self.widget_tree.get_object("MacroNameField")
        self.macro_script = self.widget_tree.get_object("MacroScript")
        self.memory_bank_vbox = self.widget_tree.get_object("MemoryBankVBox")      
        self.macros_model = self.widget_tree.get_object("MacroModel")
        self.profiles_model = self.widget_tree.get_object("ProfileModel")
        self.run_command = self.widget_tree.get_object("RunCommand")
        self.run_simple_macro = self.widget_tree.get_object("RunSimpleMacro")
        self.run_macro_script = self.widget_tree.get_object("RunMacroScript")
        self.simple_macro = self.widget_tree.get_object("SimpleMacro")
        self.command = self.widget_tree.get_object("Command")
        self.browse_for_command = self.widget_tree.get_object("BrowseForCommand")
        self.allow_combination = self.widget_tree.get_object("AllowCombination")
        self.device_model = self.widget_tree.get_object("DeviceModel")
        self.device_view = self.widget_tree.get_object("DeviceView")
        self.main_pane = self.widget_tree.get_object("MainPane")
        self.device_title = self.widget_tree.get_object("DeviceTitle")
        self.device_enabled = self.widget_tree.get_object("DeviceEnabled")
        self.tabs = self.widget_tree.get_object("Tabs")
        self.stop_service_button = self.widget_tree.get_object("StopServiceButton")
        self.driver_model = self.widget_tree.get_object("DriverModel")
        self.driver_combo = self.widget_tree.get_object("DriverCombo")
        self.global_options_button = self.widget_tree.get_object("GlobalOptionsButton")
        self.start_desktop_service_on_login = self.widget_tree.get_object("StartDesktopServiceOnLogin")
        self.start_indicator_on_login = self.widget_tree.get_object("StartIndicatorOnLogin")
        self.start_system_tray_on_login = self.widget_tree.get_object("StartSystemTrayOnLogin")
        self.macro_warning_box = self.widget_tree.get_object("MacroWarningBox")
        self.macro_edit_close_button = self.widget_tree.get_object("MacroEditCloseButton")
        self.key_table = self.widget_tree.get_object("KeyTable")
        self.key_frame = self.widget_tree.get_object("KeyFrame")
        self.macros_tab = self.widget_tree.get_object("MacrosTab")
        self.macros_tab_label = self.widget_tree.get_object("MacrosTabLabel")
        
        # Window 
        self.main_window.set_transient_for(self.parent_window)
        self.main_window.set_icon_from_file(g15util.get_app_icon(self.conf_client,  "gnome15"))
        
        # Monitor gconf
        self.conf_client.add_dir("/apps/gnome15", gconf.CLIENT_PRELOAD_NONE)
        
        # Monitor macro profiles changing
        g15profile.profile_listeners.append(self._profiles_changed)         
        
        # Configure widgets    
        self.profiles_tree.get_selection().set_mode(gtk.SELECTION_SINGLE)        
        self.macro_list.get_selection().set_mode(gtk.SELECTION_SINGLE)   

        # Indicator options
        # TODO move this out of here        
        g15util.configure_checkbox_from_gconf(self.conf_client, "/apps/gnome15/indicate_only_on_error", "OnlyShowIndicatorOnError", False, self.widget_tree, True)
        
        # Bind to events
        self.cycle_seconds.connect("value-changed", self._cycle_seconds_changed)
        self.cycle_screens.connect("toggled", self._cycle_screens_changed)
        self.site_label.connect("activate", self._open_site)
        self.plugin_tree.connect("cursor-changed", self._select_plugin)
        self.plugin_enabled_renderer.connect("toggled", self._toggle_plugin)
        self.widget_tree.get_object("PreferencesButton").connect("clicked", self._show_preferences)
        self.widget_tree.get_object("AboutPluginButton").connect("clicked", self._show_about_plugin)
        self.widget_tree.get_object("AddButton").connect("clicked", self._add_profile)
        self.widget_tree.get_object("ActivateButton").connect("clicked", self._activate)
        self.activate_on_focus.connect("toggled", self._activate_on_focus_changed)
        self.allow_combination.connect("toggled", self._allow_combination_changed)
        self.activate_by_default.connect("toggled", self._activate_on_focus_changed)
        self.clear_icon_button.connect("clicked", self._clear_icon)
        self.delete_macro_button.connect("clicked", self._remove_macro)
        self.icon_browse_button.connect("clicked", self._browse_for_icon)
        self.macro_properties_button.connect("clicked", self._macro_properties)
        self.new_macro_button.connect("clicked", self._new_macro)
        self.macro_list.connect("cursor-changed", self._select_macro)
        self.macro_name_renderer.connect("edited", self._macro_name_edited)
        self.profile_name_renderer.connect("edited", self._profile_name_edited)
        self.m1.connect("toggled", self._memory_changed)
        self.m2.connect("toggled", self._memory_changed)
        self.m3.connect("toggled", self._memory_changed)
        self.profiles_tree.connect("cursor-changed", self._select_profile)
        self.remove_button.connect("clicked", self._remove_profile)
        self.send_delays.connect("toggled", self._send_delays_changed)
        self.fixed_delays.connect("toggled", self._send_delays_changed)
        self.press_delay_adjustment.connect("value-changed", self._send_delays_changed)
        self.release_delay_adjustment.connect("value-changed", self._send_delays_changed)
        self.window_combo.child.connect("changed", self._window_name_changed)
        self.window_combo.connect("changed", self._window_name_changed)
        self.run_command.connect("toggled", self._macro_type_changed)
        self.run_simple_macro.connect("toggled", self._macro_type_changed)
        self.run_macro_script.connect("toggled", self._macro_type_changed)
        self.m1.connect("toggled", self._memory_changed)
        self.macro_name_field.connect("changed", self._macro_name_changed)
        self.command.connect("changed", self._command_changed)
        self.simple_macro.connect("changed", self._simple_macro_changed)
        self.browse_for_command.connect("clicked", self._browse_for_command)
        self.stop_service_button.connect("clicked", self._stop_service)
        self.device_view.connect("selection-changed", self._device_selection_changed)
        self.device_enabled.connect("toggled", self._device_enabled_changed)
        self.driver_combo.connect("changed", self._driver_changed)
        self.global_options_button.connect("clicked", self._show_global_options)        
        self.start_desktop_service_on_login.connect("toggled", self._change_desktop_service, "gnome15")
        self.start_indicator_on_login.connect("toggled", self._change_desktop_service, "g15-indicator")
        self.start_system_tray_on_login.connect("toggled", self._change_desktop_service, "g15-systemtray")
        
        # Connection to BAMF for running applications list
        try :
            self.bamf_matcher = self.session_bus.get_object("org.ayatana.bamf", '/org/ayatana/bamf/matcher')
        except:
            logger.warning("BAMF not available, falling back to WNCK")
            self.bamf_matcher = None            
            import wnck
            self.screen = wnck.screen_get_default()
            while gtk.events_pending():
                gtk.main_iteration()
        
        # Show infobar component to start desktop service if it is not running
        self.infobar = gtk.InfoBar()    
        self.infobar.set_size_request(-1, 64)   
        self.warning_label = gtk.Label()
        self.warning_label.set_size_request(400, -1)
        self.warning_label.set_line_wrap(True)
        self.warning_label.set_alignment(0.0, 0.0)
        self.warning_image = gtk.Image()  
        
        # Start button
        self.stop_service_button.set_sensitive(False)
        button_vbox = gtk.VBox()
        self.start_button = None
        self.start_button = gtk.Button("Start Service")
        self.start_button.connect("clicked", self._start_service)
        self.start_button.show()
        button_vbox.pack_start(self.start_button, False, False)
        
        # Populate model and configure other components
        self._load_devices()
        
        # Build the infobar content
        content = self.infobar.get_content_area()
        content.pack_start(self.warning_image, False, False)
        content.pack_start(self.warning_label, True, True)
        content.pack_start(button_vbox, False, False)  
        
        # Add the bar to the glade built UI
        self.main_vbox.pack_start(self.infobar, False, False)
        self.warning_box_shown = False
        self.infobar.hide_all()
        
        # Warning bar for macro editing
        self.macro_infobar = gtk.InfoBar()    
        self.macro_infobar.set_size_request(-1, -1)   
        self.macro_warning_label = gtk.Label()
#        self.macro_warning_label.set_size_request(200, -1)
        self.macro_warning_label.set_line_wrap(True)
#        self.macro_warning_label.set_alignment(0.0, 0.0)
        self.macro_warning_label.set_width_chars(60)
        content = self.macro_infobar.get_content_area()
        content.pack_start(self.macro_warning_label, True, True)
        self.macro_warning_box.pack_start(self.macro_infobar, True, True)
        self.macro_infobar.set_visible(False)
        
        self.gnome15_service = None

        # Watch for Gnome15 starting and stopping
        try :
            self._connect()
        except dbus.exceptions.DBusException:
            if(logger.level == logging.DEBUG):
                logger.debug("Failed to connect to service.")
                traceback.print_exc(file=sys.stdout)
            self._disconnect()
        self.session_bus.add_signal_receiver(self._name_owner_changed,
                                     dbus_interface='org.freedesktop.DBus',
                                     signal_name='NameOwnerChanged')  
        
    def run(self):
        ''' Set up everything and display the window
        '''
        self.id = None
        while True:
            opt = self.main_window.run()
            logger.debug("Option %s" % str(opt))         
            if opt != 1 and opt != 2:
                break
            
        self.main_window.hide()
        g15profile.notifier.stop()
        
    '''
    Private
    '''
        
    def _name_owner_changed(self, name, old_owner, new_owner):
        if name == "org.gnome15.Gnome15":
            if old_owner == "" and not self.connected:
                self._connect()
            elif old_owner != "" and self.connected:
                self._disconnect()
        
    def __del__(self):
        self._remove_notify_handles()
        
    def _remove_notify_handles(self):
        for h in self.notify_handles:
            self.conf_client.notify_remove(h)
            
    def _stop_service(self, event = None):
        self.gnome15_service.Stop(reply_handler = self._general_dbus_reply, error_handler = self._general_dbus_error)
        
    def _general_dbus_reply(self, *args):
        logger.info("DBUS reply %s" % str(args))

    def _general_dbus_error(self, *args):
        logger.error("DBUS error %s" % str(args))

    def _starting(self):
        logger.debug("Got starting signal")
        self.state = STARTING
        self._status_change()
        
    def _started(self):
        logger.debug("Got started signal")
        self.state = STARTED
        self._status_change()
        
    def _stopping(self):
        logger.debug("Got stopping signal")
        self.state = STOPPING
        self._status_change()
        
    def _stopped(self):
        logger.debug("Got stopped signal")
        self.state = STOPPED
        self._status_change()
            
    def _disconnect(self):
        for sig in self._signal_handles:
            self.session_bus.remove_signal_receiver(sig)
        self._signal_handles = []
        self.screen_services = {}
        self.state = STOPPED
        self._do_status_change()
        self.connected = False
        
    def _connect(self):
        self.gnome15_service = self.session_bus.get_object('org.gnome15.Gnome15', '/org/gnome15/Service')
            
        # Set initial status
        logger.debug("Getting state")
        if self.gnome15_service.IsStarting():
            logger.debug("State is starting")
            self.state = STARTING
        elif self.gnome15_service.IsStopping():
            logger.debug("State is stopping")
            self.state = STOPPING
        else:
            logger.debug("State is started")
            self.state = STARTED
            for screen_name in self.gnome15_service.GetScreens():
                logger.debug("Screen added %s" % screen_name)
                screen_service =  self.session_bus.get_object('org.gnome15.Gnome15', screen_name)
                self.screen_services[screen_name] = screen_service
        
        # Watch for changes
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._starting, dbus_interface="org.gnome15.Service", signal_name='Starting'))
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._started, dbus_interface="org.gnome15.Service", signal_name='Started'))
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._stopping, dbus_interface="org.gnome15.Service", signal_name='Stopping'))  
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._stopped, dbus_interface="org.gnome15.Service", signal_name='Stopped'))  
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._screen_added, dbus_interface="org.gnome15.Service", signal_name='ScreenAdded'))  
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._screen_removed, dbus_interface="org.gnome15.Service", signal_name='ScreenRemoved'))
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._status_change, dbus_interface="org.gnome15.Screen", signal_name='Connected'))  
        self._signal_handles.append(self.session_bus.add_signal_receiver(self._status_change, dbus_interface="org.gnome15.Screen", signal_name='Disconnected'))
        self.connected = True
        self._do_status_change()
        
    def _screen_added(self, screen_name):
        logger.debug("Screen added %s" % screen_name)
        screen_service =  self.session_bus.get_object('org.gnome15.Gnome15', screen_name)
        self.screen_services[screen_name] = screen_service
        gobject.idle_add(self._do_status_change)
        
    def _screen_removed(self, screen_name):
        logger.debug("Screen removed %s" % screen_name)
        if screen_name in self.screen_services:
            del self.screen_services[screen_name]
        self._do_status_change()
        
    def _status_change(self, arg1 = None, arg2 = None):
        gobject.idle_add(self._do_status_change)
        
    def _do_status_change(self):
        if not self.gnome15_service or self.state == STOPPED:         
            self.stop_service_button.set_sensitive(False)
            logger.debug("Stopped")
            self._show_message(gtk.MESSAGE_WARNING, "The Gnome15 desktop service is not running. It is recommended " + \
                                      "you add <b>g15-desktop-service</b> as a <i>Startup Application</i>.")
        elif self.state == STARTING:        
            logger.debug("Starting up")
            self.stop_service_button.set_sensitive(False)   
            self._show_message(gtk.MESSAGE_WARNING, "The Gnome15 desktop service is starting up. Please wait", False)
        elif self.state == STOPPING:        
            logger.debug("Stopping")                
            self.stop_service_button.set_sensitive(False)
            self._show_message(gtk.MESSAGE_WARNING, "The Gnome15 desktop service is stopping.", False)
        else:        
            logger.debug("Started - Checking status")          
            connected = 0
            first_error = ""
            for screen in self.screen_services:
                try:
                    if self.screen_services[screen].IsConnected():
                        connected += 1
                    else:
                        first_error = self.screen_services[screen].GetLastError() 
                except dbus.DBusException:
                    pass
            
            logger.debug("Found %d of %d connected" % (connected, len(self.screen_services)))
            screen_count = len(self.screen_services)
            if connected != screen_count:
                if len(self.screen_services) == 1:
                    self._show_message(gtk.MESSAGE_WARNING, "The Gnome15 desktop service is running, but failed to connect " + \
                                      "to the keyboard driver. The error message given was <b>%s</b>" % first_error, False)
                else:
                    mesg = "The Gnome15 desktop service is running, but only %d out of %d keyboards are connected. The first error message given was %s" % ( connected, screen_count, first_error )
                    self._show_message(gtk.MESSAGE_WARNING, mesg, False)
            else:
                self._hide_warning()
            self.stop_service_button.set_sensitive(True)
        
    def _hide_warning(self):
        self.warning_box_shown = False    
        self.infobar.hide_all()
        self.main_window.check_resize()
        
    def _start_service(self, widget):
        widget.set_sensitive(False)
        g15util.run_script("g15-desktop-service", ["-f"])
    
    def _show_message(self, type, text, start_service_button = True):
        self.infobar.set_message_type(type)
        if self.start_button != None:
            self.start_button.set_sensitive(True)
            self.start_button.set_visible(start_service_button)
        self.warning_label.set_text(text)
        self.warning_label.set_use_markup(True)

        if type == gtk.MESSAGE_WARNING:
            self.warning_image.set_from_stock(gtk.STOCK_DIALOG_WARNING, gtk.ICON_SIZE_DIALOG)
            self.warning_label.modify_fg(gtk.STATE_NORMAL, gtk.gdk.Color(0, 0, 0))
        
        self.main_window.check_resize()        
        self.infobar.show_all()
        if self.start_button != None and not start_service_button:
            self.start_button.hide()
        self.warning_box_shown = True
        
    def _open_site(self, widget):
        subprocess.Popen(['xdg-open',widget.get_uri()])
        
    def _to_rgb(self, string_rgb):
        rgb = string_rgb.split(",")
        return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        
    def _to_color(self, rgb):
        return gtk.gdk.Color(rgb[0] <<8, rgb[1] <<8,rgb[2] <<8)
        
    def _color_changed(self, widget, control, i):
        if i == None:
            col = widget.get_color()     
            i = ( col.red >> 8, col.green >> 8, col.blue >> 8 )
        self.conf_client.set_string(self._get_full_key(control.id), "%d,%d,%d" % ( i[0],i[1],i[2]))
        
    def _control_changed(self, widget, control):
        if control.hint & g15driver.HINT_SWITCH != 0:
            val = 0
            if widget.get_active():
                val = 1
            self.conf_client.set_int(self._get_full_key(control.id), val)
        else:
            self.conf_client.set_int(self._get_full_key(control.id), int(widget.get_value()))
    
    def _show_preferences(self, widget):
        plugin = self._get_selected_plugin()
        plugin.show_preferences(self.main_window, self.driver, self.conf_client, self._get_full_key("plugins/%s" % plugin.id))
    
    def _show_about_plugin(self, widget):
        plugin = self._get_selected_plugin()
        dialog = self.widget_tree.get_object("AboutPluginDialog")
        dialog.set_title("About %s" % plugin.name)
        dialog.run()
        dialog.hide()
        
    def _load_macro_state(self):
        device_info = g15devices.get_device_info(self.driver.get_model_name()) if self.driver is not None else None
        self.macros_tab.set_visible(device_info is not None and device_info.macros)
        self.macros_tab_label.set_visible(device_info is not None and device_info.macros)
        
    def _load_plugins(self):
        """
        Loads what drivers and plugins are appropriate for the selected
        device
        """
        self.plugin_model.clear()
        if self.selected_device:
            # Plugins appropriate
            for mod in sorted(g15pluginmanager.imported_plugins, key=lambda key: key.name):
                key = self._get_full_key("plugins/%s/enabled" % mod.id )
                if self.driver and self.driver.get_model_name() in g15pluginmanager.get_supported_models(mod):
                    enabled = self.conf_client.get_bool(key)
                    self.plugin_model.append([enabled, mod.name, mod.id])
                    if mod.id == self.selected_id:
                        self.plugin_tree.get_selection().select_path(self.plugin_model.get_path(self.plugin_model.get_iter(len(self.plugin_model) - 1)))
            if len(self.plugin_model) > 0 and self._get_selected_plugin() == None:            
                self.plugin_tree.get_selection().select_path(self.plugin_model.get_path(self.plugin_model.get_iter(0)))

        self._select_plugin(None)
        
    def _load_drivers(self):
        self.driver_model.clear()
        if self.selected_device:
            for driver_mod_key in g15drivermanager.imported_drivers:
                driver_mod = g15drivermanager.imported_drivers[driver_mod_key]
                driver = driver_mod.Driver(self.selected_device)
                if self.selected_device.model_id in driver.get_model_names():
                    self.driver_model.append((driver_mod.id, driver_mod.name))
            
        self.driver_combo.set_sensitive(len(self.driver_model) > 1)
        self._set_driver_from_configuration()
        
    def _get_selected_plugin(self):
        (model, path) = self.plugin_tree.get_selection().get_selected()
        if path != None:
            return g15pluginmanager.get_module_for_id(model[path][2])
            
    def _toggle_plugin(self, widget, path):
        plugin = g15pluginmanager.get_module_for_id(self.plugin_model[path][2])
        if plugin != None:
            key = self._get_full_key("plugins/%s/enabled" % plugin.id )
            self.conf_client.set_bool(key, not self.conf_client.get_bool(key))
            
    def _select_plugin(self, widget):       
        plugin = self._get_selected_plugin()
        if plugin != None:
            self.selected_id = plugin.id
            self.widget_tree.get_object("PluginNameLabel").set_text(plugin.name)
            self.widget_tree.get_object("DescriptionLabel").set_text(plugin.description)
            self.widget_tree.get_object("DescriptionLabel").set_use_markup(True)
            self.widget_tree.get_object("AuthorLabel").set_text(plugin.author)
            self.widget_tree.get_object("SupportedLabel").set_text(", ".join(g15pluginmanager.get_supported_models(plugin)).upper())
            self.widget_tree.get_object("CopyrightLabel").set_text(plugin.copyright)
            self.widget_tree.get_object("SiteLabel").set_uri(plugin.site)
            self.widget_tree.get_object("SiteLabel").set_label(plugin.site)
            self.widget_tree.get_object("PreferencesButton").set_sensitive(plugin.has_preferences and self.driver is not None)
            self.widget_tree.get_object("PluginDetails").set_visible(True)
        else:
            self.widget_tree.get_object("PluginDetails").set_visible(False)
            
        # List the keys that are required for each action
        for c in self.key_table.get_children():
            self.key_table.remove(c)
        actions = g15pluginmanager.get_actions(plugin)
        rows = len(actions) 
        if  rows > 0:
            self.key_table.set_property("n-rows", rows)         
        row = 0
        for action in actions:
            device_info = g15devices.get_device_info(self.driver.get_model_name())
            if action in device_info.action_keys:
                action_binding = device_info.action_keys[action]
                
                # If hold
                label = gtk.Label("")
                label.set_size_request(30, -1)
                if action_binding.state == g15driver.KEY_STATE_HELD:
                    label.set_text("<b>Hold</b>")
                    label.set_use_markup(True)
                label.set_alignment(0.0, 0.5)
                self.key_table.attach(label, 0, 1, row, row + 1,  xoptions = gtk.FILL, xpadding = 4, ypadding = 2);
                label.show()
                
                # Keys
                keys = gtk.HBox(spacing = 4)
                for k in action_binding.keys:
                    fname = os.path.abspath("%s/key-%s.png" % (g15globals.image_dir, k))
                    pixbuf = gtk.gdk.pixbuf_new_from_file(fname)
                    pixbuf = pixbuf.scale_simple(22, 14, gtk.gdk.INTERP_BILINEAR)
                    img = gtk.image_new_from_pixbuf(pixbuf)
                    img.show()
                    keys.add(img)
                keys.show()
                self.key_table.attach(keys, 1, 2, row, row + 1,  xoptions = gtk.FILL, xpadding = 4, ypadding = 2)
                
                # Text
                label = gtk.Label(actions[action])
                label.set_alignment(0.0, 0.5)
                label.show()
                self.key_table.attach(label, 2, 3, row, row + 1,  xoptions = gtk.FILL, xpadding = 4, ypadding = 2)
                row += 1
            else:
                logger.warning("Plugin %s requires an action that is not available (%s)" % ( plugin.id, action))
            
        if row > 0:
            self.key_frame.set_visible(True)
        else:   
            self.key_frame.set_visible(False)
            

    def _set_cycle_seconds_value_from_configuration(self):
        val = self.conf_client.get(self._get_full_key("cycle_seconds"))
        time = 10
        if val != None:
            time = val.get_int()
        if time != self.cycle_seconds.get_value():
            self.cycle_seconds.set_value(time)
            
    def _set_cycle_screens_value_from_configuration(self):
        val = g15util.get_bool_or_default(self.conf_client, self._get_full_key("cycle_screens"), True)
        self.cycle_seconds_widget.set_sensitive(val)
        if val != self.cycle_screens.get_active():
            self.cycle_screens.set_active(val)
            
    def _control_configuration_changed(self, client, connection_id, entry, args):
        widget = args[1]
        control = args[0]
        if isinstance(control.value, int):
            if control.hint & g15driver.HINT_SWITCH != 0:
                widget.set_active(entry.value.get_int() == 1)
            else:
                widget.set_value(entry.value.get_int())
        else:
            widget.set_color(self._to_color(self._to_rgb(entry.value.get_string())))

    def _cycle_screens_configuration_changed(self, client, connection_id, entry, args):
        self._set_cycle_screens_value_from_configuration()
        
    def _cycle_seconds_configuration_changed(self, client, connection_id, entry, args):
        self._set_cycle_seconds_value_from_configuration()
        
    def _plugins_changed(self, client, connection_id, entry, args):
        self._load_plugins()
        self._load_macro_state()
        self._load_drivers()
        
    def _cycle_screens_changed(self, widget=None):
        self.conf_client.set_bool(self._get_full_key("cycle_screens"), self.cycle_screens.get_active())
        
    def _cycle_seconds_changed(self, widget):
        val = int(self.cycle_seconds.get_value())
        self.conf_client.set_int(self._get_full_key("cycle_seconds"), val)
        
    def _create_color_icon(self, color):
        draw = gtk.Image()
        pixmap = gtk.gdk.Pixmap(None, 16, 16, 24)
        cr = pixmap.cairo_create()
        cr.set_source_rgb(float(color[0]) / 255.0, float(color[1]) / 255.0, float(color[2]) / 255.0)
        cr.rectangle(0, 0, 16, 16)
        cr.fill()
        draw.set_from_pixmap(pixmap, None)
        return draw
    
    def _active_profile_changed(self, client, connection_id, entry, args):
        self._load_profile_list()
        
    def _send_delays_changed(self, widget=None):
        if not self.adjusting:
            self.selected_profile.send_delays = self.send_delays.get_active()
            self.selected_profile.fixed_delays = self.fixed_delays.get_active()
            self.selected_profile.press_delay = int(self.press_delay_adjustment.get_value() * 1000)
            self.selected_profile.release_delay = int(self.release_delay_adjustment.get_value() * 1000)
            self.selected_profile.save()
            self._set_delay_state()
            
    def _set_delay_state(self):
        self.fixed_delays.set_sensitive(self.selected_profile.send_delays)
        self.press_delay.set_sensitive(self.selected_profile.fixed_delays and self.selected_profile.send_delays)
        self.release_delay.set_sensitive(self.selected_profile.fixed_delays and self.selected_profile.send_delays)
            
    def _change_desktop_service(self, widget, application_name):
        g15desktop.set_autostart_application(application_name, widget.get_active())
        
    def _activate_on_focus_changed(self, widget=None):
        if not self.adjusting:
            self.selected_profile.activate_on_focus = widget.get_active()        
            self.window_combo.set_sensitive(self.selected_profile.activate_on_focus)
            self.selected_profile.save()
        
    def _window_name_changed(self, widget):
        if isinstance(widget, gtk.ComboBoxEntry):
            active = widget.get_active()
            if active >= 0:
                self.window_combo.child.set_text(self.window_model[active][0])
        else:
            if widget.get_text() != self.selected_profile.window_name: 
                self.selected_profile.window_name = widget.get_text()
                if self.bamf_matcher != None:
                    for window in self.bamf_matcher.RunningApplications():
                        app = self.session_bus.get_object("org.ayatana.bamf", window)
                        view = dbus.Interface(app, 'org.ayatana.bamf.view')
                        if view.Name() == self.selected_profile.window_name:
                            icon = view.Icon()
                            if icon != None:
                                icon_path = g15util.get_icon_path(icon)
                                if icon_path != None:
                                    # We need to copy the icon as it may be temporary
                                    copy_path = os.path.join(icons_dir, os.path.basename(icon_path))
                                    shutil.copy(icon_path, copy_path)
                                    self.selected_profile.icon = copy_path
                else:                    
                    import wnck           
                    for window in wnck.screen_get_default().get_windows():
                        if window.get_name() == self.selected_profile.window_name:
                            icon = window.get_icon()
                            if icon != None:
                                filename = os.path.join(icons_dir,"%d.png" % self.selected_profile.id)
                                icon.save(filename, "png")
                                self.selected_profile.icon = filename    
                            
                self.selected_profile.save()
                
    def _driver_configuration_changed(self, *args):
        self._set_driver_from_configuration()
        self._load_plugins()
        
    def _set_driver_from_configuration(self):        
        selected_driver = self.conf_client.get_string(self._get_full_key("driver"))
        i = 0
        sel = False
        for ( id, name ) in self.driver_model:
            if id == selected_driver:
                self.driver_combo.set_active(i)
                sel = True
            i += 1
        if len(self.driver_model) > 0 and not sel:            
            self.conf_client.set_string(self._get_full_key("driver"), self.driver_model[0][0])
        else:
            controls = self.widget_tree.get_object("DriverOptionsBox")
            for c in controls.get_children():
                controls.remove(c)
            driver_mod = g15drivermanager.get_driver_mod(selected_driver)
            widget = None
            if driver_mod and driver_mod.has_preferences:
                widget = driver_mod.show_preferences(self.selected_device, controls, self.conf_client)
            if not widget:
                widget = gtk.Label("This driver has no configuration options")
            controls.pack_start(widget, False, False)
            controls.show_all()
            
    def _driver_options_changed(self):
        self._add_controls()
        self._load_plugins()
        self._load_macro_state()
        self._hide_warning()
            
    def _device_enabled_configuration_changed(self, client, connection_id, entry, args):
        self._set_enabled_value_from_configuration()
        
    def _set_enabled_value_from_configuration(self):
        enabled = g15devices.is_enabled(self.conf_client, self.selected_device) if self.selected_device != None else False
        self.device_enabled.set_active(enabled)
        self.device_enabled.set_sensitive(self.selected_device != None)
        self.tabs.set_sensitive(enabled)
                
    def _device_enabled_changed(self, widget = None):
        gobject.idle_add(self._set_device)
                
    def _driver_changed(self, widget = None):
        if len(self.driver_model) > 0:
            sel = self.driver_combo.get_active()
            if sel >= 0:
                row = self.driver_model[sel]
                current =  self.conf_client.get_string(self._get_full_key("driver"))
                if not current or row[0] != current:
                    self.conf_client.set_string(self._get_full_key("driver"), row[0])
        
    def _set_device(self):
        if self.selected_device:
            g15devices.set_enabled(self.conf_client, self.selected_device, self.device_enabled.get_active())
        
    def _memory_changed(self, widget):
        self._load_configuration(self.selected_profile)
        
    def _device_selection_changed(self, widget):
        self._load_device()
        if self.selected_device:
            self.conf_client.set_string("/apps/gnome15/config_device_name", self.selected_device.uid)
    
    def _load_device(self):
        sel_items = self.device_view.get_selected_items()
        sel_idx = sel_items[0][0] if len(sel_items) > 0 else -1
        self.selected_device = self.devices[sel_idx] if sel_idx > -1 else None
        self._remove_notify_handles()
        self.device_title.set_text(self.selected_device.model_fullname if self.selected_device else "")
        self._set_enabled_value_from_configuration()
        if self.selected_device != None:            
            self.conf_client.add_dir(self._get_device_conf_key(), gconf.CLIENT_PRELOAD_NONE)   
            self.notify_handles.append(self.conf_client.notify_add(self._get_full_key("cycle_seconds"), self._cycle_seconds_configuration_changed));
            self.notify_handles.append(self.conf_client.notify_add(self._get_full_key("cycle_screens"), self._cycle_screens_configuration_changed));
            self.notify_handles.append(self.conf_client.notify_add(self._get_full_key("plugins"), self._plugins_changed))
            self.notify_handles.append(self.conf_client.notify_add(self._get_full_key("active_profile"), self._active_profile_changed))
            self.notify_handles.append(self.conf_client.notify_add(self._get_full_key("enabled"), self._device_enabled_configuration_changed))
            self.notify_handles.append(self.conf_client.notify_add(self._get_full_key("driver"), self._driver_configuration_changed))
            self.selected_profile = g15profile.get_active_profile(self.selected_device)  
            self._set_cycle_seconds_value_from_configuration()
            self._set_cycle_screens_value_from_configuration()
            
        self._add_controls()
        self.main_window.show_all()
        self._load_profile_list()
        self._load_plugins()
        self._load_macro_state()
        if self.selected_device:
            self._load_drivers()
        self._do_status_change()
        
    def _get_device_conf_key(self):
        return "/apps/gnome15/%s" % self.selected_device.uid
    
    def _get_full_key(self, key):
        return "%s/%s" % (self._get_device_conf_key(), key)
        
    def _select_profile(self, widget):
        (model, path) = self.profiles_tree.get_selection().get_selected()
        self.selected_profile = g15profile.get_profile(self.selected_device, model[path][2])
        self._load_configuration(self.selected_profile)
        
    def _select_macro(self, widget):
        self._set_available_actions()
        
    def _set_available_actions(self):
        (model, path) = self.macro_list.get_selection().get_selected()
        self.delete_macro_button.set_sensitive(path != None)
        self.macro_properties_button.set_sensitive(path != None)
        
    def _activate(self, widget):
        (model, path) = self.profiles_tree.get_selection().get_selected()
        self._make_active(g15profile.get_profile(self.selected_device,  model[path][2]))
        
    def _make_active(self, profile): 
        profile.make_active()
        self._load_profile_list()
        
    def _clear_icon(self, widget):
        self.selected_profile.icon = ""            
        self.selected_profile.save()
        
    def _browse_for_icon(self, widget):
        dialog = gtk.FileChooserDialog("Open..",
                               None,
                               gtk.FILE_CHOOSER_ACTION_OPEN,
                               (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dialog.set_default_response(gtk.RESPONSE_OK)
        dialog.set_transient_for(self.main_window)
        dialog.set_filename(self.selected_profile.icon)
        filter = gtk.FileFilter()
        filter.set_name("All files")
        filter.add_pattern("*")
        
        dialog.add_filter(filter)
        
        filter = gtk.FileFilter()
        filter.set_name("Images")
        filter.add_mime_type("image/png")
        filter.add_mime_type("image/jpeg")
        filter.add_mime_type("image/gif")
        filter.add_pattern("*.png")
        filter.add_pattern("*.jpg")
        filter.add_pattern("*.jpeg")
        filter.add_pattern("*.gif")
        dialog.add_filter(filter)
        
        response = dialog.run()
        
        if response == gtk.RESPONSE_OK:
            self.selected_profile.icon = dialog.get_filename()            
            self.selected_profile.save()
            
        dialog.destroy()
        
    def _remove_profile(self, widget):
        dialog = self.widget_tree.get_object("ConfirmRemoveProfileDialog")  
        dialog.set_transient_for(self.main_window)
        response = dialog.run()
        dialog.hide()
        if response == 1:
            active_profile = g15profile.get_active_profile(self.selected_device)
            if self.selected_profile.id == active_profile.id:
                self._make_active(g15profile.get_profile(0))
            self.selected_profile.delete()
            self._load_profile_list()
            
    def _macro_name_changed(self, widget):
        self.editing_macro.name = widget.get_text()
        self.editing_macro.save()
        
    def _allow_combination_changed(self, widget):
        if not self.adjusting and not self.allow_combination.get_active():
            for button in self.key_buttons:
                if len(self.editing_macro.keys) > 1:
                    button.set_active(False)
            self._check_macro(self.editing_macro.keys)
            
    def _toggle_key(self, widget, key, macro):
        keys = list(macro.keys) 
                
        if key in keys:
            keys.remove(key)
        else:            
            if not self.adjusting and not self.allow_combination.get_active():
                for button in self.key_buttons:
                    if button != widget:
                        self.adjusting = True
                        try :
                            button.set_active(False)
                        finally:
                            self.adjusting = False
                for ikey in keys:
                    if ikey != key:
                        keys.remove(ikey)
            keys.append(key)
            
        if not self.selected_profile.are_keys_in_use(self._get_memory_number(), keys, exclude = [self.editing_macro]):
            macro.set_keys(keys)
        
        if not self.adjusting:
            self._check_macro(keys)
        
    def _check_macro(self, keys):
        if len(keys) > 0:
            memory = self._get_memory_number()        
            reserved = g15devices.are_keys_reserved(self.driver.get_model_name(), keys)
            in_use = self.selected_profile.are_keys_in_use(memory, keys, exclude = [self.editing_macro])
            if in_use:
                self.macro_infobar.set_message_type(gtk.MESSAGE_ERROR)
                self.macro_warning_label.set_text("This key combination is already in use with "+ \
                                                  "another macro. Please choose a different key or combination of keys")
                self.macro_infobar.set_visible(True)
                self.macro_infobar.show_all()       
                self.macro_edit_close_button.set_sensitive(False)      
                return       
            elif reserved:
                self.macro_infobar.set_message_type(gtk.MESSAGE_WARNING)
                self.macro_warning_label.set_text("This key combination is reserved for use with an action. You "+ \
                                                  "may use it, but the results are undefined.")
                self.macro_infobar.set_visible(True)
                self.macro_infobar.show_all()
                self.macro_edit_close_button.set_sensitive(True)      
                return       
            
        print "Hiding"
        self.macro_infobar.set_visible(False)
        self.macro_edit_close_button.set_sensitive(True)
        
    def _new_macro(self, widget):
        memory = self._get_memory_number()
        
        # Find the next free G-Key
        use = None
        for row in self.driver.get_key_layout():
            if not use:
                for key in row:                
                    reserved = g15devices.are_keys_reserved(self.driver.get_model_name(), list(key))
                    in_use = self.selected_profile.are_keys_in_use(memory, list(key))
                    if not in_use and not reserved:
                        use = key
                        break
                    
        if use:
            macro = self.selected_profile.create_macro(memory, [use], "Macro %s" % " ".join(g15util.get_key_names([use])), g15profile.MACRO_SIMPLE, "")
            self._edit_macro(macro)
        else:
            logger.warning("No free keys")
        
    def _macro_properties(self, widget):
        self._edit_macro(self._get_selected_macro())
        
    def _get_selected_macro(self):        
        (model, path) = self.macro_list.get_selection().get_selected()
        if model and path:
            key_list_key = model[path][2]
            return self.selected_profile.get_macro(self._get_memory_number(), g15profile.get_keys_from_key(key_list_key))
        
    def _edit_macro(self, macro):
        self.editing_macro = macro
        memory = self._get_memory_number()
        dialog = self.widget_tree.get_object("EditMacroDialog")  
        dialog.set_transient_for(self.main_window)
        keys_frame = self.widget_tree.get_object("KeysFrame")
        self.allow_combination.set_active(len(self.editing_macro.keys) > 1)
        
        # Build the G-Key selection widget
        if self.rows:
            keys_frame.remove(self.rows)
        self.rows = gtk.VBox()
        self.rows.set_spacing(4)
        self.key_buttons = []
        for row in self.driver.get_key_layout():
            hbox = gtk.HBox()
            hbox.set_spacing(4)
            for key in row:
                key_name = g15util.get_key_names([ key ])
                g_button = gtk.ToggleButton(" ".join(key_name))
                g_button.key = key
                g_button.set_active(key in self.editing_macro.keys)
                g_button.connect("toggled", self._toggle_key, key, self.editing_macro)
                self.key_buttons.append(g_button)
                hbox.pack_start(g_button, True, True)
            self.rows.pack_start(hbox, False, False)
        keys_frame.add(self.rows)     
        keys_frame.show_all()
        
        
        # Set the type of macro
        if self.editing_macro.type == g15profile.MACRO_COMMAND:
            self.run_command.set_active(True)
        elif self.editing_macro.type == g15profile.MACRO_SIMPLE:
            self.run_simple_macro.set_active(True)
        elif self.editing_macro.type == g15profile.MACRO_SCRIPT:
            self.run_macro_script.set_active(True)            
        self._set_available_options()
            
        # Set the other details 
        self.memory_bank_label.set_text("M%d" % memory)
        self.macro_name_field.set_text(self.editing_macro.name)
        self.simple_macro.set_text(self.editing_macro.simple_macro)
        self.macro_name_field.grab_focus()
        text_buffer = gtk.TextBuffer()
        text_buffer.set_text(self.editing_macro.macro)        
        text_buffer.connect("changed", self._macro_script_changed)
        self.macro_script.set_buffer(text_buffer)
        self._check_macro(self.editing_macro.keys)
        
                        
        dialog.run()
        dialog.hide()
        self.editing_macro.name = self.macro_name_field.get_text()
        self._load_profile_list()
        
    def _remove_macro(self, widget):
        memory = self._get_memory_number()
        (model, path) = self.macro_list.get_selection().get_selected()
        key_list_key = model[path][2]
        dialog = self.widget_tree.get_object("ConfirmRemoveMacroDialog") 
        dialog.set_transient_for(self.main_window)
        response = dialog.run()
        dialog.hide()
        if response == 1:
            keys = g15profile.get_keys_from_key(key_list_key)
            self.selected_profile.delete_macro(memory, keys)
            self._load_profile_list()
            
    def _show_global_options(self, widget):        
        dialog = self.widget_tree.get_object("GlobalOptionsDialog")
        
        self.widget_tree.get_object("OnlyShowIndicatorOnError").set_visible(g15desktop.is_desktop_application_installed("g15-indicator"))
        self.start_indicator_on_login.set_visible(g15desktop.is_desktop_application_installed("g15-indicator"))
        self.start_system_tray_on_login.set_visible(g15desktop.is_desktop_application_installed("g15-systemtray"))
        self.start_desktop_service_on_login.set_active(g15desktop.is_autostart_application("gnome15"))
        self.start_indicator_on_login.set_active(g15desktop.is_autostart_application("g15-indicator"))
        self.start_system_tray_on_login.set_active(g15desktop.is_autostart_application("g15-systemtray"))
        dialog.set_transient_for(self.main_window)
        dialog.run()
        dialog.hide()
        
    def _add_profile(self, widget):
        dialog = self.widget_tree.get_object("AddProfileDialog") 
        dialog.set_transient_for(self.main_window) 
        response = dialog.run()
        dialog.hide()
        if response == 1:
            new_profile_name = self.widget_tree.get_object("NewProfileName").get_text()
            new_profile = g15profile.G15Profile(self.selected_device, -1)
            new_profile.name = new_profile_name
            g15profile.create_profile(new_profile)
            self.selected_profile = new_profile
            self._load_profile_list()
        
    def _get_memory_number(self):
        if self.m1.get_active():
            return 1
        elif self.m2.get_active():
            return 2
        elif self.m3.get_active():
            return 3
        
    def _load_devices(self):
        self.device_model.clear()
        self.devices = g15devices.find_all_devices()
        sel_device_name = self.conf_client.get_string("/apps/gnome15/config_device_name")
        idx = 0
        for device in self.devices:
            if device.model_id == 'virtual':
                icon_file = g15util.get_icon_path(["preferences-system-window", "preferences-system-windows", "gnome-window-manager", "window_fullscreen"])
            else:
                icon_file = g15util.get_app_icon(self.conf_client,  device.model_id)
            pixb = gtk.gdk.pixbuf_new_from_file(icon_file)
            self.device_model.append([pixb.scale_simple(96, 96, gtk.gdk.INTERP_BILINEAR), device.model_fullname, 96, gtk.WRAP_WORD, pango.ALIGN_CENTER])
            if not sel_device_name or device.uid == sel_device_name:
                sel_device_name = device.uid
                self.device_view.select_path((idx,))
            idx += 1
            
        if idx == 1:
            main_parent = self.main_pane.get_parent() 
            main_parent.remove(self.main_pane)
            self.main_vbox.reparent(main_parent)
            self.widget_tree.get_object("DeviceDetails").set_visible(False)
            self.device_title.set_visible(False)
            self.device_enabled.set_visible(False)
            self.device_enabled.set_active(True)
            
        
    def _load_profile_list(self):
        current_selection = self.selected_profile
        self.profiles_model.clear()        
        if self.selected_device != None:
            tree_selection = self.profiles_tree.get_selection()
            active = g15profile.get_active_profile(self.selected_device)
            active_id = -1
            if active != None:
                active_id = active.id
            self.selected_profile = None
            self.profiles = g15profile.get_profiles(self.selected_device)
            for profile in self.profiles: 
                weight = 400
                if profile.id == active_id:
                    weight = 700
                self.profiles_model.append([profile.name, weight, profile.id, profile.name != "Default" ])
                if current_selection != None and profile.id == current_selection.id:
                    tree_selection.select_path(self.profiles_model.get_path(self.profiles_model.get_iter(len(self.profiles_model) - 1)))
                    self.selected_profile = profile
            if self.selected_profile != None:                             
                self._load_configuration(self.selected_profile)             
            elif len(self.profiles) > 0:            
                tree_selection.select_path(self.profiles_model.get_path(self.profiles_model.get_iter(0)))
            else:
                default_profile = g15profile.G15Profile(self.selected_device, "Default")
                g15profile.create_profile(default_profile)
                self._load_profile_list()
            
        
    def _profiles_changed(self, device_uid, macro_profile_id):        
        gobject.idle_add(self._load_profile_list)
        
    def _profile_name_edited(self, widget, row, value):        
        profile = self.profiles[int(row)]
        if value != profile.name:
            profile.name = value
            profile.save()
        
    def _macro_name_edited(self, widget, row, value):
        macro = self._get_sorted_list()[int(row)] 
        if value != macro.name:
            macro.name = value
            macro.save()
            self._load_configuration(self.selected_profile)
        
    def _get_sorted_list(self):
        return sorted(self.selected_profile.macros[self._get_memory_number() - 1], key=lambda key: key.key_list_key)
        
    def _load_configuration(self, profile):
        self.adjusting = True
        try : 
            current_selection = self._get_selected_macro()        
            tree_selection = self.macro_list.get_selection()
            name = profile.window_name
            if name == None:
                name = ""            
            self.macros_model.clear()
            selected_macro = None
            macros = self._get_sorted_list()
            for macro in macros:
                self.macros_model.append([", ".join(g15util.get_key_names(macro.keys)), macro.name, macro.key_list_key, True])
                if current_selection != None and macro.key_list_key == current_selection.key_list_key:
                    tree_selection.select_path(self.macros_model.get_path(self.macros_model.get_iter(len(self.macros_model) - 1)))
                    selected_macro = macro        
            if selected_macro == None and len(macros) > 0:            
                tree_selection.select_path(self.macros_model.get_path(self.macros_model.get_iter(0)))
                    
            self.activate_on_focus.set_active(profile.activate_on_focus)
            self.activate_by_default.set_active(profile.activate_on_focus)
            if profile.window_name != None:
                self.window_combo.child.set_text(profile.window_name)
            else:
                self.window_combo.child.set_text("")
            self.send_delays.set_active(profile.send_delays)
            self.fixed_delays.set_active(profile.fixed_delays)
            self._set_delay_state()
            self.press_delay_adjustment.set_value(float(profile.press_delay) / 1000.0)
            self.release_delay_adjustment.set_value(float(profile.release_delay) / 1000.0)
            self.window_combo.set_sensitive(self.activate_on_focus.get_active())
            
            if profile.icon == None or profile.icon == "":
                self.profile_icon.set_from_stock(gtk.STOCK_MISSING_IMAGE, gtk.ICON_SIZE_DIALOG)
            else:
                self.profile_icon.set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(profile.icon, 48, 48))
            
            if profile.get_default():
                self.window_combo.set_visible(False)
                self.activate_on_focus.set_visible(False)
                self.window_label.set_visible(False)
                self.activate_by_default.set_visible(True)
                self.remove_button.set_sensitive(False)
            else:
                self._load_windows()
                self.window_combo.set_visible(True)
                self.activate_on_focus.set_visible(True)
                self.window_label.set_visible(True)
                self.activate_by_default.set_visible(False)
                self.remove_button.set_sensitive(True)
                
            if self.color_button != None:
                rgb = profile.get_mkey_color(self._get_memory_number())
                if rgb == None:
                    self.enable_color_for_m_key.set_active(False)
                    self.color_button.set_sensitive(False)
                    self.color_button.set_color(g15util.to_color((255, 255, 255)))
                else:
                    self.color_button.set_sensitive(True)
                    self.color_button.set_color(g15util.to_color(rgb))
                    self.enable_color_for_m_key.set_active(True)
                
            self._set_available_actions()
        finally:
            self.adjusting = False
            
    def _load_windows(self):
        self.window_model.clear()   
        if self.bamf_matcher != None:            
            for window in self.bamf_matcher.RunningApplications():
                app = self.session_bus.get_object("org.ayatana.bamf", window)
                view = dbus.Interface(app, 'org.ayatana.bamf.view')
                self.window_model.append([view.Name(), window])
        else:
            apps = {}
            for window in self.screen.get_windows():
                if not window.is_skip_pager():
                    app = window.get_application()                
                    if app and not app.get_name() in apps:
                        apps[app.get_name()] = app
            for app in apps:
                self.window_model.append([app, app])
                
    def _simple_macro_changed(self, widget):
        self.editing_macro.simple_macro = widget.get_text()
        self.editing_macro.save()
        
    def _macro_script_changed(self, buffer):
        self.editing_macro.macro = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter())
        self.editing_macro.save()
                
    def _command_changed(self, widget):
        self.editing_macro.command = widget.get_text()
        self.editing_macro.save()
        
    def _set_available_options(self):
        self.command.set_sensitive(self.run_command.get_active())
        self.browse_for_command.set_sensitive(self.run_command.get_active())
        self.simple_macro.set_sensitive(self.run_simple_macro.get_active())
        self.macro_script.set_sensitive(self.run_macro_script.get_active())
        
    def _browse_for_command(self, widget):
        dialog = gtk.FileChooserDialog("Open..",
                               None,
                               gtk.FILE_CHOOSER_ACTION_OPEN,
                               (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dialog.set_default_response(gtk.RESPONSE_OK)
        filter = gtk.FileFilter()
        filter.set_name("All files")
        filter.add_pattern("*")
        dialog.add_filter(filter)
        response = dialog.run()
        while gtk.events_pending():
            gtk.main_iteration(False) 
        if response == gtk.RESPONSE_OK:
            self.command.set_text(dialog.get_filename())
        dialog.destroy() 
        return False
                
    def _macro_type_changed(self, widget):
        if self.run_command.get_active():
            self.editing_macro.type = g15profile.MACRO_COMMAND
        elif self.run_simple_macro.get_active():
            self.editing_macro.type = g15profile.MACRO_SIMPLE
        else:
            self.editing_macro.type = g15profile.MACRO_SCRIPT
        self.editing_macro.save()
        self._set_available_options()
        
    def _add_controls(self):
                
        # Remove previous notify handles
        for nh in self.control_notify_handles:
            self.conf_client.notify_remove(nh)            
        
        driver_controls = None
        if self.selected_device != None:
            # Driver. We only need this to get the controls. Perhaps they should be moved out of the driver
            # class and the values stored separately
            try :
                self.driver = g15drivermanager.get_driver(self.conf_client, self.selected_device)
                self.driver.on_driver_options_change = self._driver_options_changed
                
                # Controls
                driver_controls = self.driver.get_controls()
            except Exception as e:
                logger.error("Failed to load driver to query controls. %s" % str(e))
            
        if not driver_controls:
            driver_controls = []
        
        # Remove current components
        controls = self.widget_tree.get_object("ControlsBox")
        for c in controls.get_children():
            controls.remove(c)
        for c in self.memory_bank_vbox.get_children():
            self.memory_bank_vbox.remove(c)
        self.memory_bank_vbox.add(self.widget_tree.get_object("MemoryBanks"))
        
        # Slider and Color controls            
        table = gtk.Table(rows = len(driver_controls), columns = 2)
        table.set_row_spacings(4)
        row = 0
        for control in driver_controls:
            val = control.value
            if isinstance(val, int):  
                if ( control.hint & g15driver.HINT_SWITCH ) == 0 and ( control.hint & g15driver.HINT_MKEYS ) == 0:
                    label = gtk.Label(control.name)
                    label.set_alignment(0.0, 0.5)
                    label.show()
                    table.attach(label, 0, 1, row, row + 1,  xoptions = gtk.FILL, xpadding = 8, ypadding = 4);
                    
                    hscale = gtk.HScale()
                    hscale.set_value_pos(gtk.POS_RIGHT)
                    hscale.set_digits(0)
                    hscale.set_range(control.lower,control.upper)
                    hscale.set_value(control.value)
                    hscale.connect("value-changed", self._control_changed, control)
                    hscale.show()
                    
                    table.attach(hscale, 1, 2, row, row + 1, xoptions = gtk.EXPAND | gtk.FILL);                
                    self.control_notify_handles.append(self.conf_client.notify_add(self._get_full_key(control.id), self._control_configuration_changed, [ control, hscale ]))
            else:  
                label = gtk.Label(control.name)
                label.set_alignment(0.0, 0.5)
                label.show()
                table.attach(label, 0, 1, row, row + 1,  xoptions = gtk.FILL, xpadding = 8, ypadding = 4);
                
                hbox = gtk.Toolbar()
                hbox.set_style(gtk.TOOLBAR_ICONS)
                for i in [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255) ]:
                    button = gtk.Button()
                    button.set_image(self._create_color_icon(i))
#                    button.modify_bg(gtk.STATE_NORMAL, gtk.gdk.Color(i[0] <<8,i[1]  <<8,i[2]  <<8))
#                    button.modify_bg(gtk.STATE_PRELIGHT, gtk.gdk.Color(i[0] <<8,i[1]  <<8,i[2]  <<8))
                    button.connect("clicked", self._color_changed, control, i)
                    hbox.add(button)
                    button.show()
                color_button = gtk.ColorButton()
                
                color_button.connect("color-set", self._color_changed, control, None)
                color_button.show()
                color_button.set_color(self._to_color(control.value))
                hbox.add(color_button)
                self.control_notify_handles.append(self.conf_client.notify_add(self._get_full_key(control.id), self._control_configuration_changed, [ control, color_button]));
                
                hbox.show()
                table.attach(hbox, 1, 2, row, row + 1);
                
            row += 1
        controls.add(table)
          
        # Switch controls  
        controls = self.widget_tree.get_object("SwitchesBox")
        for c in controls.get_children():
            controls.remove(c)
        table.set_row_spacings(4)
        row = 0
        for control in driver_controls:
            val = control.value
            if isinstance(val, int):  
                if control.hint & g15driver.HINT_SWITCH != 0:
                    check_button = gtk.CheckButton(control.name)
                    check_button.set_alignment(0.0, 0.0)
                    check_button.show()
                    controls.pack_start(check_button, False, False, 4)  
                    check_button.connect("toggled", self._control_changed, control)
                    self.notify_handles.append(self.conf_client.notify_add(self._get_full_key(control.id), self._control_configuration_changed, [ control, check_button ]));
                    row += 1
        
        # Hide the cycle screens if the device has no screen
        if self.driver != None and self.driver.get_bpp() == 0:            
            self.cycle_screens.hide()
            self.cycle_screens_options.hide()
        else:            
            self.cycle_screens.show()
            self.cycle_screens_options.show()
        
        # If the keyboard has a colour dimmer, allow colours to be assigned to memory banks
        control = self.driver.get_control_for_hint(g15driver.HINT_DIMMABLE) if self.driver != None else None
        if control != None and not isinstance(control.value, int):
            hbox = gtk.HBox()
            self.enable_color_for_m_key = gtk.CheckButton("Set backlight colour")
            self.enable_color_for_m_key.connect("toggled", self._color_for_mkey_enabled)
            hbox.pack_start(self.enable_color_for_m_key, True, False)            
            self.color_button = gtk.ColorButton()
            self.color_button.set_sensitive(False)                
            self.color_button.connect("color-set", self._profile_color_changed)
#            color_button.set_color(self._to_color(control.value))
            hbox.pack_start(self.color_button, True, False)
            self.memory_bank_vbox.add(hbox)
            hbox.show_all()
        else:
            self.color_button = None
            self.enable_color_for_m_key = None
            
        if row == 0:
            self.widget_tree.get_object("SwitchesFrame").hide() 
            
    def _profile_color_changed(self, widget):
        if not self.adjusting:
            self.selected_profile.set_mkey_color(self._get_memory_number(), 
                                                 g15util.color_to_rgb(widget.get_color()) if self.enable_color_for_m_key.get_active() else None)
            self.selected_profile.save()
    
    def _color_for_mkey_enabled(self, widget):
        self.color_button.set_sensitive(widget.get_active())        
        self._profile_color_changed(self.color_button)