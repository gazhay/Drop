#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, GdkPixbuf
from gi.repository import GObject

try:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as AppIndicator
except:
    from gi.repository import AppIndicator

import re,subprocess,socket
import shutil
import urllib.parse,time,os,signal,sys
from random import randint
from zeroconf import ServiceBrowser, Zeroconf
from contextlib import suppress

tempsock = "/tmp/drop"

# TODO
#
VERSION   = "0.1a"
ICONDIR   = "./DropIcons"
DEVMODE   = True
GetMyUser = subprocess.run("who | awk '{print $1}'", shell=True, stdout=subprocess.PIPE)
DropUser  = GetMyUser.stdout.decode("utf8").rstrip()
DropRoot  = "/home/"+DropUser+"/Drop/"
DropLand  = DropRoot+"Landed/"
DropStage = DropRoot+".staging/"

## Do our required directories exist?

def makeUserFolder(folderName):
    os.mkdir(folderName, 0o0755)
    shutil.chown(folderName, user=DropUser, group=DropUser)

try:
    if not os.path.isdir(DropRoot ): makeUserFolder(DropRoot)
    if not os.path.isdir(DropLand ): makeUserFolder(DropLand)
    if not os.path.isdir(DropStage): makeUserFolder(DropStage)
except:
    print("Could not create home directories")
    exit()

## Utility functions
def shellquote(s):
    return "'" + s.replace("'", "'\\''") + "'"

def get_resource_path(rel_path):
    dir_of_py_file = os.path.dirname(__file__)
    rel_path_to_resource = os.path.join(dir_of_py_file, rel_path)
    abs_path_to_resource = os.path.abspath(rel_path_to_resource)
    return abs_path_to_resource

def alert(msg):
    parent = None
    md = Gtk.MessageDialog(parent, 0, Gtk.MessageType.INFO, Gtk.ButtonsType.CLOSE, msg)
    md.run()
    md.destroy()

class Modes:
    IDLE = 0
    DROP = 1
    SEND = 2
    RECV = 3

MYHOSTNAME=""
if socket.gethostname().find('.')>=0:
    MYHOSTNAME=socket.gethostname()
else:
    MYHOSTNAME=socket.gethostbyaddr(socket.gethostname())[0]


# ############################################################################## Indicator
class IndicatorDrop:
    statusIcons = [ "dropicon", "droprecv", "dropsend0", "dropsend1", "dropsend2", "dropsend3", "dropsend4", "dropsend5", "dropsend6", "dropsend7", "dropsend8" ]
    lastpoll    = None

    def __init__(self):
        self.ind = AppIndicator.Indicator.new("indicator-drop", self.statusIcons[0], AppIndicator.IndicatorCategory.SYSTEM_SERVICES)
        self.ind.set_icon_theme_path( get_resource_path(ICONDIR) )
        self.ind.set_icon( self.statusIcons[0] )
        self.ind.set_status (AppIndicator.IndicatorStatus.ACTIVE)
        self.mode = Modes.IDLE

        # have to give indicator a menu
        self.menu = Gtk.Menu()

        self.addMenuItem( self.menu, "About...", self.aboutDialog)
        if DEVMODE:
            self.addMenuItem( self.menu, "Restart", self.reboot)
        self.addSeperator(self.menu)
        self.addMenuItem(self.menu, "Exit", self.handler_menu_exit )

        self.menu.show()
        self.ind.set_menu(self.menu)
        GLib.timeout_add_seconds(1, self.handler_timeout)

    def addSeperator(self, menu):
        item = Gtk.SeparatorMenuItem()
        item.show()
        menu.append(item)

    def addMenuItem(self, menu, label, handler):
        item = Gtk.MenuItem()
        item.set_label(label)
        item.connect("activate", handler)
        item.show()
        menu.append(item)

    def addRadioMenu(self, menu, label):
        item = Gtk.CheckMenuItem(label=label)
        item.set_active(is_active=False)
        # item.connect("activate", self.toggleMe)
        item.show()
        menu.append(item)

    def addSubMenu(self, menu, label):
        pass

    def aboutDialog(self, evt):
        dlg = Gtk.AboutDialog();
        dlg.set_name("About...")
        dlg.set_program_name("Drop")
        dlg.set_version(VERSION)
        dlg.set_comments("""
Simple transfers across LAN with avahi
        """)
        dlg.set_authors(['Gareth Hay'])
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(get_resource_path(ICONDIR)+"/"+self.statusIcons[0]+".png" , 100, 100)
        dlg.set_logo(pixbuf)
        dlg.show()

    def reboot(self, evt):
        Gtk.main_quit()
        os.execv(__file__, sys.argv)

    def handler_menu_exit(self, evt):
        self.exit()

    def fileCheck(self):
        # check for os err on ls
        self.lastpoll=time.time()
        try:
            fileList = subprocess.run("\ls "+DropRoot+"*/*", stdout=subprocess.PIPE, shell=True)
            # If we haven't thrown an error, there could be files
            if fileList.returncode!=0:
                print("No files found")
            else:
                print("Must move the following to .staging")
                print(fileList.stdout)
                print("Should now copy everything")
        except err:
            print("No files found")
            return True

    def handler_timeout(self):
        #set icon based on self.mode
        # every x time poll directories
        if (self.lastpoll==None) or ((time.time() - self.lastpoll)>30):
            self.fileCheck()

        if self.mode==Modes.IDLE:
            self.ind.set_icon( self.statusIcons[0] )
        elif self.mode==Modes.DROP:
            self.ind.set_icon( self.statusIcons[0] )
        elif self.mode==Modes.SEND:
            # TODO %
            self.ind.set_icon( self.statusIcons[2] )
        elif self.mode==Modes.RECV:
            # ANimate this
            self.ind.set_icon( self.statusIcons[1] )

        return True

    def main(self):
        #  attempt multiprocess shenanigans
        Gtk.main()

    def exit(self):
        Gtk.main_quit()

# ############################################################################## Avahi
class AvahiListener(object):
    target = ""
    Hosts = None

    def __init__(self):
        self.Hosts=[]
        service = """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
 <name replace-wildcards="yes">%%h</name>
  <service>
   <type>_drop-target._tcp</type>
   <port>58769</port>
   <txt-record>path=/home/%s/Drop/</txt-record>
  </service>
</service-group>
""" % (DropUser)
        try:
            servfile = open("/etc/avahi/services/Drop.service", "w")
            print(service, file=servfile)
            servfile.close()
        except:
            print("You are probably running as something other than root")
            exit()


    def remove_service(self, zeroconf, type, name):
        for host in self.Hosts:
            if host.get("name")== name:
                info = host

        print("Removing %s" % info['info'].server)
        os.remove(DropRoot+info['info'].server)
        self.Hosts.remove(info)

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        # subitem = Gtk.CheckMenuItem()
        print("new server %s \n me %s" % (info.server, MYHOSTNAME))
        if info.server == MYHOSTNAME+".local.":
            print("Not adding myself")
        else:
            newServ = DropRoot+info.server
            os.mkdir(newServ, 0o0755)
            shutil.chown(newServ, user=DropUser, group=DropUser)
            self.Hosts.append({"name": name, "info": info})

    def setTarget(self, targetobj):
        self.target = targetobj

    def unpublish(self):
        os.remove("/etc/avahi/services/Drop.service")

# ############################################################################## Main

if __name__ == "__main__":
    try:
        ind      = IndicatorDrop() # Basic Menu
        # # AVAHI PUBLISH
        zeroconf = Zeroconf()
        listener = AvahiListener() # Should publish me
        listener.setTarget(ind);   # Allow crosstalk
        # # AVAHI LISTEN
        browser  = ServiceBrowser(zeroconf, "_drop-target._tcp.local.", listener) # find siblings
        ind.main()

    except Exception as e:
        print(e)
        with suppress(Exception):
          listener.unpublish()
          ind.exit()
        exit()
    finally:
        with suppress(Exception):
          listener.unpublish()
          ind.exit()
        print("Bye.")
