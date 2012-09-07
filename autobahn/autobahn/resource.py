###############################################################################
##
##  Copyright 2012 Tavendo GmbH
##
##  Licensed under the Apache License, Version 2.0 (the "License");
##  you may not use this file except in compliance with the License.
##  You may obtain a copy of the License at
##
##      http://www.apache.org/licenses/LICENSE-2.0
##
##  Unless required by applicable law or agreed to in writing, software
##  distributed under the License is distributed on an "AS IS" BASIS,
##  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
##  See the License for the specific language governing permissions and
##  limitations under the License.
##
###############################################################################

__all__ = ("WebSocketResource","HTTPChannelHixie76Aware",)

from zope.interface import implements

from twisted.python import log
from twisted.protocols.policies import ProtocolWrapper
try:
   from twisted.web.error import NoResource
except:
   ## starting from Twisted 12.2, NoResource has moved
   from twisted.web.resource import NoResource  
from twisted.web.error import UnsupportedMethod
from twisted.web.resource import IResource
from twisted.web.server import NOT_DONE_YET
from twisted.web.http import HTTPChannel

from websocket import WebSocketServerFactory, WebSocketServerProtocol


class HTTPChannelHixie76Aware(HTTPChannel):
   """
   Hixie-76 is deadly broken. It includes 8 bytes of body, but then does not
   set content-length header. This hacked HTTPChannel injects the missing
   HTTP header upon detecting Hixie-76. We need this since otherwise
   Twisted Web will silently ignore the body.

   To use this, set twisted.web.server.Site.protocol = HTTPChannelHixie76Aware
   """

   def headerReceived(self, line):
      header = line.split(':')[0].lower()
      if header == "sec-websocket-key1" and not self._transferDecoder:
         HTTPChannel.headerReceived(self, "Content-Length: 8")
      HTTPChannel.headerReceived(self, line)



class WebSocketResource(object):
   """
   A Twisted Web resource for WebSocket. This resource needs to be instantiated
   with a factory derived from WebSocketServerFactory.
   """

   implements(IResource)

   isLeaf = True

   def __init__(self, factory):
      """
      Ctor.

      :param factory: An instance of WebSocketServerFactory.
      :type factory: obj
      """
      self._factory = factory


   def getChildWithDefault(self, name, request):
      """
      This resource cannot have children, hence this will always fail.
      """
      return NoResource("No such child resource.")


   def putChild(self, path, child):
      """
      This resource cannot have children, hence this is always ignored.
      """
      pass


   def render(self, request):
      """
      Render the resource. This will takeover the transport underlying
      the request, create a WebSocketServerProtocol and let that do
      any subsequent communication.
      """

      ## Create Autobahn WebSocket protocol.
      ##
      protocol = self._factory.buildProtocol(request.transport.getPeer())
      if not protocol:
         ## If protocol creation fails, we signal "internal server error"
         request.setResponseCode(500)
         return ""

      ## Take over the transport from Twisted Web
      ##
      transport, request.transport = request.transport, None

      ## Connect the transport to our protocol. Once #3204 is fixed, there
      ## may be a cleaner way of doing this.
      ## http://twistedmatrix.com/trac/ticket/3204
      ##
      if isinstance(transport, ProtocolWrapper):
         ## i.e. TLS is a wrapping protocol
         transport.wrappedProtocol = protocol
      else:
         transport.protocol = protocol
      protocol.makeConnection(transport)

      ## We recreate the request and forward the raw data. This is somewhat
      ## silly (since Twisted Web already did the HTTP request parsing
      ## which we will do a 2nd time), but it's totally non-invasive to our
      ## code. Maybe improve this.
      ##
      data = "%s %s HTTP/1.1\x0d\x0a" % (request.method, request.path)
      for h in request.requestHeaders.getAllRawHeaders():
         data += "%s: %s\x0d\x0a" % (h[0], ",".join(h[1]))
      data += "\x0d\x0a"
      data += request.content.read() # we need this for Hixie-76
      protocol.dataReceived(data)

      return NOT_DONE_YET
