#!/usr/bin/python3
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
import socket
from threading import Thread

class AvahiListener(object):

    def remove_service(self, zeroconf, type, name):
        print("Service %s removed" % (name,))

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        print("new server %s " % (info.server))

zeroconf = Zeroconf()
listener = AvahiListener() # Should publish me
# # AVAHI LISTEN
# browser  = ServiceBrowser(zeroconf, "_drop-target._tcp.local.", listener) # find siblings
browser = ServiceBrowser(zeroconf, "_drop-target._tcp.local.", listener)
# browser.daemon =True
# browser.start()

while 1:
    pass
