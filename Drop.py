#!/usr/bin/env python3

#                                     ,..
#                                 _,-'   `-,_
#                              ,,'          ``._
#                           ,-'                ``..
#                       _,-'  ____                 `-.
#                    ,-'     |  _ \ _ __ ___  _ __    `-._
#                _,-'        | | | | '__/ _ \| '_ \       `..
#              .'            | |_| | | | (_) | |_) |         `.,_
#            ,-..            |____/|_|  \___/| .__/          _,-'
#            |   `-._                        |_|          ,,'   |
#            |       `..                              _.-'      |
#            |          `-._                       ,-'          |
#            |              `-._               _.-'             |
#            |                 '`..         ,-'                 |
#            |                     `-._ _,-'                    |
#            |                         '                        |
#            |                         |                        |
#            |                         |                        |
#            |                         |                        |
#            |                         |                        |
#            |                         |                        |
#            |                         |                        |
#            |                         |                        |
#             `,_                      |                      ,-'
#                -'.                   |                  _,-'
#                   ``,_               |               ,,'
#                       --.            |            ,-'
#                          `.._        |        _.-'
#                               .      |     ,.'
#                                ``-,  |  ,-'
#                                    `-'`'
#
#
# A Hacky Lan based linux file transfer program.
#
# Using avahi we discover local clients, and create a local directory for them
#     Usually ~/Drop/otherclients.local.
#
# Dropping files into this will transfer them to the client's landing directory
#     Usually ~/Drop/Landed
#
# Control the daemon via the AppIndicator menu
#
#
# TODO
# - Check subdirectory works - might dwonload, not create subdirs
# - Decode bonjour/avahi hosts extra info
#    - Should probably have shortname fqdn etc
# - Preferences
# - Progress
# - check file is not copying

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, GdkPixbuf
from gi.repository import GObject
from gi.repository import Gio

try:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as AppIndicator
except:
    from gi.repository import AppIndicator

import re,subprocess,socket
import shutil, glob
import time,os,signal,sys
import json
from urllib.parse import quote, unquote
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
from contextlib import suppress
from threading import Thread

# tempsock = "/tmp/drop"
GetMyUser    = subprocess.run("who | awk '{print $1}' | head -n 1", shell=True, stdout=subprocess.PIPE)
polldelay    = 5

def get_resource_path(rel_path):
    dir_of_py_file = os.path.dirname(__file__)
    rel_path_to_resource = os.path.join(dir_of_py_file, rel_path)
    abs_path_to_resource = os.path.abspath(rel_path_to_resource)
    return abs_path_to_resource

VERSION      = "0.8 sleepyBeta"
ICONDIR      = get_resource_path("./DropIcons")
CONFIGFL     = os.path.abspath(os.path.expanduser("~/.drop.conf"))
HUP          = get_resource_path(__file__)
configfuse   = False
if not os.path.isfile(CONFIGFL):
    with open(CONFIGFL, "w+") as cfg:
        data = {}
        json.dump(data, cfg)
        configfuse = True

with open(CONFIGFL, "r") as cfg:
    try:
        data = json.load(cfg)
    except:
        data = {}
    print(data)
    DEVMODE      = data.get("DEVMODE", True)
    DropUser     = GetMyUser.stdout.decode("utf8").rstrip()
    DropRoot     = data.get("DropRoot"    , "/home/"+DropUser+"/Drop/")
    DropLand     = data.get("DropLand"    , DropRoot+"Landed/"        )
    DropStage    = data.get("DropStage"   , DropRoot+".staging/"      )
    DropPort     = data.get("DropPort"    , 58769                     )
    TranPort     = data.get("TranPort"    , DropPort+1                )
    ActualDelete = data.get("ActualDelete", True                      )
    SleepyBeta   = data.get("SleepyBeta"  , True                      )

mainAppInd   = None

if configfuse:
    with open(CONFIGFL, "w+") as cfg:
        data = {
            "DEVMODE"      : DEVMODE,
            "DropUser"     : DropUser,
            "DropRoot"     : DropRoot,
            "DropLand"     : DropLand,
            "DropStage"    : DropStage,
            "DropPort"     : DropPort,
            "TranPort"     : TranPort,
            "ActualDelete" : ActualDelete,
            "SleepyBeta"   : SleepyBeta
        }
        json.dump(data, cfg)
    if DEVMODE: print("Wrote default config %s" % CONFIGFL)

if SleepyBeta:
    print("Sleepy imports")
    import dbus # For sleep branch
    from dbus.mainloop.glib import DBusGMainLoop

def customiconPlease(folderName, iconname=None):
    folder = Gio.File.new_for_path(folderName)
    if folderName==DropRoot:
        icon_file = Gio.File.new_for_path(ICONDIR+"/dropicon.png")
    elif folderName==DropLand:
        icon_file = Gio.File.new_for_path(ICONDIR+"/droprecv.png")
    elif iconname!=None:
        icon_file = Gio.File.new_for_path(ICONDIR+"/"+iconname)
    else:
        icon_file = None
    info = folder.query_info('metadata::custom-icon', 0, None)
    if icon_file is not None:
        icon_uri = icon_file.get_uri()
        info.set_attribute_string('metadata::custom-icon', icon_uri)
    else:
        # Change the attribute type to INVALID to unset it
        info.set_attribute('metadata::custom-icon',
            Gio.FileAttributeType.INVALID, '')
    # Write the changes back to the file
    folder.set_attributes_from_info(info, 0, None)

def makeUserFolder(folderName, iconname=None):
    try:
        os.mkdir(folderName, 0o0755)
        customiconPlease(folderName, iconname=iconname)
    except:
        pass
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

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.13.13.13', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

MYIPADDR=get_ip()
print("DISCOVERED LOCAL IP = %s " % MYIPADDR)
# ############################################################################## Threaded Web comms
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
import pycurl

# HTTPRequestHandler class
class TransferHandler(BaseHTTPRequestHandler):

    def getFromRemote(self,snp, path, fname):
        mainAppInd.mode = Modes.RECV
        if "/" in fname:
            needDirs = os.path.dirname(fname)
            needDirs = DropLand+needDirs
            print("GonnaCreate '%s'" % needDirs)
            try:
                os.makedirs( needDirs, exist_ok=True)
            except:
                print("Things are gonna get foobar now, cannot create sub dirs.")
        c = pycurl.Curl()
        if not "local" in path:
            path = path.split(".")[0]+".local."
        ccmd = 'http://'+snp+"/"+path+"/"+quote(fname)
        print("Will ask for '"+ccmd+"'")
        c.setopt(c.URL, ccmd)
        with open(DropLand+fname, 'wb') as f:
            c.setopt(c.WRITEFUNCTION, f.write)
            c.setopt(c.NOPROGRESS, False) #reqd for below
            c.setopt(c.XFERINFOFUNCTION, mainAppInd.transferProgress)
#            c.setopt(c.PROGRESSFUNCTION, mainAppInd.transferProgress)
            c.perform()
        cmd = "curl http://"+snp.split(":")[0]+":"+str(DropPort)+"/?DropDone="+quote(fname)
        ping = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
        print(ping.stdout.decode("utf8"))
        return

    def do_GET(self):
        # if 'http:' in self.headers.getheaders('referer'): #moducum of security
        (servername, serverport) = self.client_address
        try:
            servername = socket.gethostbyaddr(servername)[0] # return .lan
            if "." in servername: # Fudge for local dns
                splitup = servername.split(".")
                servername = splitup[0]+".local."
        except:
            pass
        try:
            if self.path.startswith("/?DropPing"):
                self.send_response(200)
                fetchMe = unquote(self.path[11:])
                print("fetchme = '"+fetchMe+"'")
                self.send_header('Content-type','text/plain')
                self.end_headers()
                message = "DropPing!"
                self.wfile.write(bytes(message, "utf8"))
                self.getFromRemote("%s:%d" % (servername,TranPort), MYHOSTNAME, fetchMe)
            elif self.path.startswith("/?DropDone"):
                self.send_response(200)
                fetchMe = unquote(self.path[11:])
                self.send_header('Content-type','text/plain')
                self.end_headers()
                message = "DropDone!"
                self.wfile.write(bytes(message, "utf8"))
                mainAppInd.doneCopy(DropRoot+servername+"/"+fetchMe)
            else:
                self.send_response(404)
                self.end_headers()
                return
            # self.flush()
        except Exception as e:
            print(e)
            with suppress(Exception):
                self.finish()
                self.connection.close()
        finally:
            return

def run_on(port, chdir=None, indic=None):
    server_address = ('0.0.0.0', port)
    if not chdir==None:
        os.chdir(chdir)
        httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    else:
        httpd = HTTPServer(server_address, TransferHandler)
    print("["+MYHOSTNAME+"]"+"[T]Starting a server on port %i" % port)
    httpd.serve_forever()

# ############################################################################## Threaded Copier
class FileDrop(Thread):
    def __init__(self, srcfile, callback):
        Thread.__init__(self)
        self.callback = callback
        self.srcfile  = srcfile

    def run(self):
        guessserver = self.srcfile.replace(DropRoot,"")
        (servername,junk,residualpath) = guessserver.partition("/")
        # New thinking.
        print("Path = '"+residualpath+"'")
        cmd = "curl http://"+servername+":"+str(DropPort)+"/?DropPing="+quote(residualpath)
        ping = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
        print(ping.stdout.decode("utf8"))
        # self.callback(self.srcfile)

# ############################################################################## Indicator
class IndicatorDrop:
    statusIcons = [ "dropicon", "droprecv", "dropsend0", "dropsend1", "dropsend2", "dropsend3", "dropsend4", "dropsend5", "dropsend6", "dropsend7", "dropsend8" ]
    lastpoll    = None
    filequeue   = []
    inprogress  = None
    arrivals    = False
    Hosts       = []
    hostitem    = None
    hasSlept    = None

    def hostdiscover(self, hostname):
        if not hostname in self.Hosts:
            self.Hosts.append(hostname)
        self.hostmenu()

    def hostlost(self, hostname):
        if hostname in self.Hosts:
            self.Hosts.remove(hostname)
        self.hostmenu()

    def nullHandler(self, evt):
        pass

    def checkActive(self, mi):
        if len(self.Hosts)==0:
            mi.set_sensitive(False)
        else:
            # Only become acive when hosts are available
            mi.set_sensitive(True)
        mi.show()

    def hostmenu(self):
        submenu = Gtk.Menu()
        self.checkActive(self.hostitem)
        for host in self.Hosts:
            self.addMenuItem(submenu, host, self.sendToHost)
        submenu.show()
        self.hostitem.set_submenu( submenu )

    def sendToHost(self, evt):
        dialog = Gtk.FileChooserDialog("Please choose a file", None,
            Gtk.FileChooserAction.OPEN,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
             Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        targetHost = evt.get_label()
        response = dialog.run()
        ff = dialog.get_filename()
        dialog.destroy()
        time.sleep(0.1)
        if response == Gtk.ResponseType.OK:
            nameonly = os.path.basename(ff)
            shutil.copyfile(ff, DropRoot+targetHost+"/"+nameonly)
            return
        elif response == Gtk.ResponseType.CANCEL:
            print("Cancel clicked")

    def __init__(self):
        self.ind = AppIndicator.Indicator.new("indicator-drop", self.statusIcons[0], AppIndicator.IndicatorCategory.SYSTEM_SERVICES)
        self.ind.set_icon_theme_path( ICONDIR )
        self.ind.set_icon( self.statusIcons[0] )
        self.ind.set_status (AppIndicator.IndicatorStatus.ACTIVE)
        self.mode = Modes.IDLE

        self.menu = Gtk.Menu()

        self.addMenuItem( self.menu, "About...", self.aboutDialog)
        if DEVMODE:
            self.addMenuItem( self.menu, " ~ Restart", self.reboot)
        self.addSeperator(self.menu)
        self.hostitem = self.addMenuItem(self.menu, "Send To Host", self.nullHandler )
        self.addSeperator(self.menu)
        self.addMenuItem( self.menu, "Open Drop Folder", self.openDrop   )
        self.addMenuItem( self.menu, "Clear Drops"     , self.clearDrops )
        self.addSeperator(self.menu)
        self.addMenuItem(self.menu, "Exit", self.handler_menu_exit )

        self.menu.show()
        self.ind.set_menu(self.menu)
        GLib.timeout_add_seconds(1, self.handler_timeout)

    def openDrop(self,evt):
        subprocess.run("xdg-open "+DropRoot, shell=True)
        return

    def clearDrops(self,evt):
        shutil.rmtree(DropLand+"*")

    def addSeperator(self, menu):
        item = Gtk.SeparatorMenuItem()
        item.show()
        menu.append(item)
        return item

    def addMenuItem(self, menu, label, handler):
        item = Gtk.MenuItem()
        item.set_label(label)
        item.connect("activate", handler)
        item.show()
        menu.append(item)
        return item

    def addRadioMenu(self, menu, label, handler=None):
        item = Gtk.CheckMenuItem(label=label)
        item.set_active(is_active=False)
        if handler!=None:
            item.connect("activate", handler)
        # item.connect("activate", self.toggleMe)
        item.show()
        menu.append(item)
        return item

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
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(ICONDIR+"/"+self.statusIcons[0]+".png" , 100, 100)
        dlg.set_logo(pixbuf)
        dlg.show()

    def reboot(self, evt):
        Gtk.main_quit()
        print(HUP)
        os.execv(HUP, sys.argv)

    def handler_menu_exit(self, evt):
        self.exit()

    def pushToQueue(self, item):
        if item not in self.filequeue:
            self.filequeue.append(item)

    def popovQueue(self, item):
        if item in self.filequeue:
            self.filequeue.remove(item)

    def rCheck(self, thisroot):
        thisreturn = []
        files = glob.glob(thisroot+"/*")
        for afile in files:
            if os.path.isdir(afile):
                print("Recursion into "+(afile))
                thisreturn.extend(self.rCheck(afile))
            else:
                print("Ordinary File "+afile)
                thisreturn.append(afile)
        return thisreturn


    def fileCheck(self):
        # check for os err on ls
        self.lastpoll=time.time()
        # files = glob.glob(DropRoot+"*/*")
        files = self.rCheck(DropRoot+"*")
        self.arrivals=False
        self.mode=Modes.IDLE
        for afile in files:
            if not "Landed/" in afile:
                self.pushToQueue(afile)
            else:
                self.arrivals=True

    def nullcallback(self):
        pass

    def doneCopy(self, srcname):
        self.mode=Modes.IDLE
        try:
            if not ActualDelete:
                nameonly = os.path.basename(srcname)
                os.rename(srcname, DropRoot+".staging/"+nameonly)
            else:
                os.remove( srcname )
            # need to rmdir directories if empty
            if os.listdir(os.path.dirname(srcname))==[]:
                try:
                    os.rmdir(os.path.dirname(srcname))
                except:
                    print("Dir not empty ")
        except:
            print("Could not remove physical file '"+srcname+"'")
        self.popovQueue( srcname )
        self.inprogress = None

    def transferProgress(self, total_to_download, total_downloaded, total_to_upload, total_uploaded):
      if total_to_download:
        percent_completed = float(total_downloaded)/total_to_download       # You are calculating amount uploaded
        rate = round(percent_completed * 100, ndigits=2)                # Convert the completed fraction to percentage
        completed = "#" * int(rate)                                     # Calculate completed percentage
        spaces = " " * int( 100 - percent_completed)                               # Calculate remaining completed rate
        print('[%s%s] %s%%' %(completed, spaces, rate))      # the pretty progress [####     ] 34%
        sys.stdout.flush()

    def handler_timeout(self):
        #set icon based on self.mode
        # every x time poll directories
        # Should really go with pyinotify or some such
        try:
            if (self.lastpoll==None) or ((time.time() - self.lastpoll))>polldelay:
                self.fileCheck()
                if self.hasSlept:
                    listener.publish()
                    self.hasSlept=False

            if len(self.filequeue)>0:
                self.mode = Modes.SEND
                if self.inprogress == None:
                    self.inprogress = self.filequeue[0]
                    # no need to thread this. let's inline it.
                    FileDrop(self.inprogress, self.doneCopy).run()
                    # copy.run()
            if self.arrivals:
                self.mode=Modes.RECV

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

            self.checkActive(self.hostitem)
            return True
        except KeyboardInterrupt:
            return False

    def attachControl(self, cHttp):
        self.control = cHttp

    def main(self):
        #  attempt multiprocess shenanigans
        try:
            GLib.MainLoop().run()
            #https://bugzilla.gnome.org/show_bug.cgi?id=622084#c4
            # Gtk.main()
        except KeyboardInterrupt:
            self.exit()
            return

    def exit(self):
        Gtk.main_quit()

# ############################################################################## Avahi
class AvahiListener(object):
    target = ""
    Hosts = []
    info  = None

    def __init__(self):
        desc = {'version': VERSION, "self_id_ip": MYIPADDR}

        self.info = ServiceInfo("_drop-target._tcp.local.",
                           "_"+MYHOSTNAME+"._drop-target._tcp.local.",
                           socket.inet_aton(MYIPADDR), DropPort, 0, 0,
                           desc, MYHOSTNAME+".local.")

    def cleanUpDir(self, dirname):
        guessname = dirname.split(".")[0]+".local."
        # print("We want to remove "+guessname)
        try:
            shutil.rmtree(DropRoot+dirname, ignore_errors=True)
            return True
        except Exception as e:
            # print(e)
            return False

    def cleanAll(self):
        for host in self.Hosts:
            print("Remove %s" % host.get("name"))
            self.cleanUpDir(host.get("name"))

    def remove_service(self, zeroconf, type, name):
        # print("Service %s removed" % (name,))
        for host in self.Hosts:
            # if host.get("name")== name:
            info = host
            # print(info)
            print("[-] Removing %s" % info['info'].server)
            self.cleanUpDir(info['info'].server)
            self.Hosts.remove(info)
            mainAppInd.hostlost(info['info'].server)

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        # subitem = Gtk.CheckMenuItem()
        if info.server == MYHOSTNAME+".local.":
            # print("Not adding myself")
            pass
        else:
            newServ = DropRoot+info.server
            makeUserFolder(newServ, iconname="dropicon.png")
            # os.mkdir(newServ, 0o0755)
            # shutil.chown(newServ, user=DropUser, group=DropUser)
            self.Hosts.append({"name": name, "info": info})
            mainAppInd.hostdiscover(info.server)
        print("[+] Adding %s " % (info.server))

    def setTarget(self, targetobj):
        self.target = targetobj

    def setZC(self, targetZC):
        self.zc = targetZC
        self.publish()

    def publish(self):
        self.publishedas = self.info
        try:
            self.zc.register_service(self.info)
        except:
            print("Publish error - most likely non uniquer")

    def unpublish(self):
        self.cleanAll()
        self.zc.unregister_service(self.publishedas)
        # self.zc.close()
        # os.remove("/etc/avahi/services/Drop.service")

# ############################################################################## Main
global listener # Be sensible here once beta is over

def handle_sleep(*args):
    print("Handling Sleep Event")
    listener.unpublish()
    mainAppInd.hasSlept=True

if __name__ == "__main__":
    try:
        mainAppInd = IndicatorDrop() # Basic Menu
        # # AVAHI PUBLISH
        zeroconf = Zeroconf()
        listener = AvahiListener() # Should publish me
        listener.setTarget(mainAppInd);   # Allow crosstalk
        listener.setZC(zeroconf)
        # # AVAHI LISTEN
        # browser  = ServiceBrowser(zeroconf, "_drop-target._tcp.local.", listener) # find siblings
        browser = Thread(target=ServiceBrowser, args=[zeroconf, "_drop-target._tcp.local.", listener])
        browser.daemon =True
        browser.start()
        # # HTTPd for control messages = overkill
        control = Thread(target=run_on, args=[DropPort])
        control.daemon = True # Do not make us wait for you to exit
        control.start()
        # # Httpd for file transfers
        server = Thread(target=run_on, args=[TranPort,DropRoot])
        server.daemon = True # Do not make us wait for you to exit
        server.start()
        if SleepyBeta:
            print("Configuring sleep handler")
            DBusGMainLoop(set_as_default=True)     # integrate into gobject main loop
            bus = dbus.SystemBus()                 # connect to system wide dbus
            bus.add_signal_receiver(               # define the signal to listen to
                handle_sleep,                      # callback function
                'PrepareForSleep',                 # signal name
                'org.freedesktop.login1.Manager',  # interface
                'org.freedesktop.login1'           # bus name
            )
            # bus.add_signal_receiver(           # defince the signal to listen to
            #     handle_resume,            # name of callback function
            #     'Resuming',                        # singal name
            #     'org.freedesktop.UPower',          # interface
            #     'org.freedesktop.UPower'           # bus name
            # )
        # Let's go
        mainAppInd.main()
        print("Begin Shutdown")
        # with suppress(Exception):
        Gtk.main_quit()
        listener.unpublish()
        mainAppInd.exit()
    except KeyboardInterrupt:
        with suppress(Exception):
            Gtk.main_quit()
            listener.unpublish()
            mainAppInd.exit()
    except Exception as e:
        print(e)
    finally:
        print("Bye.")
        quit()
