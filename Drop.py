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
import shutil, glob
import urllib.parse,time,os,signal,sys
from random import randint
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
from contextlib import suppress
from threading import Thread

tempsock = "/tmp/drop"

# TODO
#
VERSION   = "0.1a"
ICONDIR   = "./DropIcons"
DEVMODE   = True
GetMyUser = subprocess.run("who | awk '{print $1}'", shell=True, stdout=subprocess.PIPE)
DropUser  = GetMyUser.stdout.decode("utf8").rstrip()
print("Local user will be %s" % DropUser)
DropRoot  = "/home/"+DropUser+"/Drop/"
DropLand  = DropRoot+"Landed/"
DropStage = DropRoot+".staging/"
DropPort  = 58769

## Do our required directories exist?

# import sys
# from gi.repository import Gio
#
# if len(sys.argv) not in (2, 3):
#     print 'Usage: {} FOLDER [ICON]'.format(sys.argv[0])
#     print 'Leave out ICON to unset'
#     sys.exit(0)
#
# folder = Gio.File.new_for_path(sys.argv[1])
# icon_file = Gio.File.new_for_path(sys.argv[2]) if len(sys.argv) == 3 else None
#
# # Get a file info object
# info = folder.query_info('metadata::custom-icon', 0, None)
#
# if icon_file is not None:
#     icon_uri = icon_file.get_uri()
#     info.set_attribute_string('metadata::custom-icon', icon_uri)
# else:
#     # Change the attribute type to INVALID to unset it
#     info.set_attribute('metadata::custom-icon',
#         Gio.FileAttributeType.INVALID, '')
#
# # Write the changes back to the file
# folder.set_attributes_from_info(info, 0, None)

def makeUserFolder(folderName):
    try:
        os.mkdir(folderName, 0o0755)
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

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.13.13.13', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

MYIPADDR=get_ip()
print(" LOCAL IP = %s " % MYIPADDR)
# ############################################################################## Threaded Web comms
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler

# HTTPRequestHandler class
class TransferHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        # if 'http:' in self.headers.getheaders('referer'):
        self.send_response(200)
        (servername, serverport) = self.client_address
        if servername=="127.0.0.1":
            print("I AM %s" % MYHOSTNAME)
        print("[TransferHandler] servername %s" % servername)
        try:
            servername = socket.gethostbyaddr(servername)[0] # return .lan
            if "." in servername:
                splitup = servername.split(".")
                servername = splitup[0]+".local."
        except:
            pass
        dserver = "%s:%d" % (servername,DropPort)
        print("dserver: %s" % dserver)
        print("+++ PATH RECV ++++ %s " % self.path)
        try:
            if self.path.startswith("/?DropPing"):
                fetchMe = self.path[11:]
                print("["+MYHOSTNAME+"]"+"<<<< Ping Recevied from %s" % dserver)
                print("["+MYHOSTNAME+"]"+"<<<< To fetch %s" % fetchMe)
                # Send headers
                self.send_header('Content-type','text/plain')
                self.end_headers()
                message = "Thanks!"
                self.wfile.write(bytes(message, "utf8"))
                self.finish()
                self.connection.close()
                # Dialback and get a dilelist
                cmd="curl http://"+dserver+"/"+MYHOSTNAME+".local./"+fetchMe+" -o "+DropLand+fetchMe
                print("["+MYHOSTNAME+"]"+">>>>%s<<<<" % cmd)
                curllist   = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
                print(curllist.stdout.decode("utf8"))
            else:
                # If its not an explicit command, then its a file request, so serve it
                # self.path = '/'+servername+'/'+self.path
                # servern name is .lan
                print("["+MYHOSTNAME+"]"+"[OVERRIDE] sending to "+DropRoot+servername+"/"+self.path)
                inf = open(DropRoot+self.path[1:])
                self.wfile.write(inf.read())
                inf.close()
                # return SimpleHTTPServer.SimpleHTTPRequestHandler.do_GET(self)
            # self.flush()
        except Exception as e:
            print("["+MYHOSTNAME+"]"+"[T]Error")
            print(e)
            with suppress(Exception):
                self.finish()
                self.connection.close()
        finally:
            return

def run_on(port):
    print("["+MYHOSTNAME+"]"+"[T]Starting a server on port %i" % port)
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, TransferHandler)
    httpd.serve_forever()

# ############################################################################## Threaded Copier
class FileDrop(Thread):
    def __init__(self, srcfile, callback):
        Thread.__init__(self)
        self.callback = callback
        self.srcfile  = srcfile

    def run(self):
        print("["+MYHOSTNAME+"]"+"[T]Seperate Thread to copy %s" % self.srcfile)
        time.sleep(10)
        guessserver = self.srcfile.replace(DropRoot,"")
        (servername,junk,residualpath) = guessserver.partition("/")
        # New thinking.
        cmd = "curl http://"+servername+":"+str(DropPort)+"/?DropPing="+residualpath
        print(">>>>%s<<<<" % cmd)
        ping = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
        print(ping.stdout.decode("utf8"))
        print("[T]Sim copy over")
        self.callback(self.srcfile)

# ############################################################################## Indicator
class IndicatorDrop:
    statusIcons = [ "dropicon", "droprecv", "dropsend0", "dropsend1", "dropsend2", "dropsend3", "dropsend4", "dropsend5", "dropsend6", "dropsend7", "dropsend8" ]
    lastpoll    = None
    filequeue   = []
    inprogress  = None

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

    def pushToQueue(self, item):
        if item not in self.filequeue:
            print("Adding %s" % item)
            self.filequeue.append(item)

    def popovQueue(self, item):
        if item in self.filequeue:
            self.filequeue.remove(item)

    def fileCheck(self):
        # check for os err on ls
        self.lastpoll=time.time()
        files = glob.glob(DropRoot+"*/*")
        for afile in files:
            self.pushToQueue(afile)

    def doneCopy(self, srcname):
        os.remove( srcname )
        self.popovQueue( srcname )
        self.inprogress = None

    def handler_timeout(self):
        #set icon based on self.mode
        # every x time poll directories
        if (self.lastpoll==None) or ((time.time() - self.lastpoll)>30):
            self.fileCheck()

        if len(self.filequeue)>0:
            print("My current transfer queue is ")
            print(self.filequeue)
            if self.inprogress == None:
                self.inprogress = self.filequeue[0]
                FileDrop(self.inprogress, self.doneCopy).run()
                # copy.run()

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
    info  = None

    def __init__(self):
        desc = {'path': DropRoot, "realip": MYIPADDR}

        self.info = ServiceInfo("_drop-target._tcp.local.",
                           "_"+MYHOSTNAME+"._drop-target._tcp.local.",
                           socket.inet_aton(MYIPADDR), DropPort, 0, 0,
                           desc, MYHOSTNAME+".local.")

    def cleanUpDir(self, dirname):
        try:
            os.remove(DropRoot+dirname)
            return True
        except:
            return False

    def cleanAll(self):
        for host in self.Hosts:
            print("Remove %s" % host.get("name"))
            self.cleanUpDir(host.get("name"))

    def remove_service(self, zeroconf, type, name):
        for host in self.Hosts:
            if host.get("name")== name:
                info = host

        print("Removing %s" % info['info'].server)
        self.cleanUpDir(info['info'].server)
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

    def setZC(self, targetZC):
        self.zc = targetZC
        self.zc.register_service(self.info)

    def unpublish(self):
        self.cleanAll()
        self.zc.unregister_service(info)
        self.zc.close()
        # os.remove("/etc/avahi/services/Drop.service")

# ############################################################################## Main

if __name__ == "__main__":
    try:
        ind      = IndicatorDrop() # Basic Menu
        # # AVAHI PUBLISH
        zeroconf = Zeroconf()
        listener = AvahiListener() # Should publish me
        listener.setTarget(ind);   # Allow crosstalk
        listener.setZC(zeroconf)
        # # AVAHI LISTEN
        browser  = ServiceBrowser(zeroconf, "_drop-target._tcp.local.", listener) # find siblings
        # # HTTPd for file transfers
        server = Thread(target=run_on, args=[DropPort])
        server.daemon = True # Do not make us wait for you to exit
        server.start()
        ind.main()
    except KeyboardInterrupt:
        with suppress(Exception):
          listener.unpublish()
          ind.exit()
          exit()
    except Exception as e:
        print(e)
        ind.exit()
    finally:
        with suppress(Exception):
          listener.unpublish()
          ind.exit()
        print("Bye.")
