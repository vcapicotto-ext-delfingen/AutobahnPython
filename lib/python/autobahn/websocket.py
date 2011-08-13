###############################################################################
##
##  Copyright 2011 Tavendo GmbH
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

from twisted.internet import reactor, protocol
from twisted.python import log
import binascii
import hashlib
import base64
import struct
import random
import urlparse
import os
import cStringIO
import StringIO
from io import BytesIO


class FrameHeader:

   def __init__(self, opcode, fin, rsv, length, mask, mask_array):
      self.opcode = opcode
      self.fin = fin
      self.rsv = rsv
      self.length = length
      self.ptr = 0
      self.mask = mask
      self.mask_array = mask_array


class WebSocketProtocol(protocol.Protocol):
   """
   A Twisted Protocol class for WebSockets. This class is used by both WebSocket
   client and server protocol version. It is unusable standalone, for example
   the WebSockets initial handshake is implemented in derived class differently
   for clients and servers.
   """

   ## magic used during WebSocket handshake
   ##
   WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

   MESSAGE_TYPE_TEXT = 1
   """WebSockets text message type (UTF-8 payload)."""

   MESSAGE_TYPE_BINARY = 2
   """WebSockets binary message type (arbitrary binary payload)."""

   ## WebSockets protocol state
   ##
   STATE_CLOSED = 0
   STATE_CONNECTING = 1
   STATE_CLOSING = 2
   STATE_OPEN = 3

   ## Streaming Send State
   SEND_STATE_GROUND = 0
   SEND_STATE_MESSAGE_BEGIN = 1
   SEND_STATE_INSIDE_MESSAGE = 2
   SEND_STATE_INSIDE_MESSAGE_FRAME = 3

   ## WebSockets protocol close codes
   ##
   CLOSE_STATUS_CODE_NORMAL = 1000
   """Normal close of connection."""

   CLOSE_STATUS_CODE_GOING_AWAY = 1001
   """Going away."""

   CLOSE_STATUS_CODE_PROTOCOL_ERROR = 1002
   """Protocol error."""

   CLOSE_STATUS_CODE_PAYLOAD_NOT_ACCEPTED = 1003
   """Payload not accepted."""

   CLOSE_STATUS_CODE_FRAME_TOO_LARGE = 1004
   """Frame too large."""

   CLOSE_STATUS_CODE_NULL = 1005 # MUST NOT be set in close frame!
   """No status received. (MUST NOT be used as status code when sending a close)."""

   CLOSE_STATUS_CODE_CONNECTION_LOST = 1006 # MUST NOT be set in close frame!
   """Abnormal close of connection. (MUST NOT be used as status code when sending a close)."""

   CLOSE_STATUS_CODE_TEXT_FRAME_NOT_UTF8 = 1007
   """Invalid UTF-8."""


   def onOpen(self):
      """
      Callback when initial WebSockets handshake was completed. Now you may send messages.
      Default implementation does nothing. Override in derived class.
      """
      if self.debug:
         log.msg("WebSocketProtocol.onOpen")


   def onMessageBegin(self, opcode):
      """
      Callback when receiving a new message has begun. Default implementation will
      prepare to buffer message frames. Override in derived class.

      :param opcode: Opcode of message.
      :type opcode: int
      """
      self.message_opcode = opcode
      self.message_data = []


   def onMessageFrameBegin(self, length, reserved):
      """
      Callback when receiving a new message frame has begun. Default implementation will
      prepare to buffer message frame data. Override in derived class.

      :param length: Length of message frame which is received.
      :type length: int
      :param reserved: Reserved bits set in frame (an integer from 0 to 7).
      :type reserved: int
      """
      self.frame_length = length
      self.frame_reserved = reserved
      self.frame_data = []


   def onMessageFrameData(self, payload):
      """
      Callback when receiving data witin message frame. Default implementation will
      buffer data for frame. Override in derived class.

      :param payload: Partial payload for message frame.
      :type payload: str
      """
      self.frame_data.append(payload)


   def onMessageFrameEnd(self):
      """
      Callback when a message frame has been completely received. Default implementation
      will flatten the buffered frame data and callback onMessageFrame. Override
      in derived class.
      """
      data = bytearray().join(self.frame_data)
      self.logRxFrame(self.current_frame.fin, self.current_frame.rsv, self.current_frame.opcode, self.current_frame.mask is not None, self.current_frame.length, self.current_frame.mask, data)
      self.onMessageFrame(data, self.frame_reserved)


   def onMessageFrame(self, payload, reserved):
      """
      Callback fired when complete message frame has been received. Default implementation
      will buffer frame for message. Override in derived class.

      :param payload: Message frame payload.
      :type payload: str
      :param reserved: Reserved bits set in frame (an integer from 0 to 7).
      :type reserved: int
      """
      self.message_data.append(payload)


   def onMessageEnd(self):
      """
      Callback when a message has been completely received. Default implementation
      will flatten the buffered frames and callback onMessage. Override
      in derived class.
      """
      data = bytearray().join(self.message_data)
      self.onMessage(str(data), self.message_opcode == WebSocketProtocol.MESSAGE_TYPE_BINARY)
      self.message_opcode = None
      self.message_data = None


   def onMessage(self, payload, binary):
      """
      Callback when a complete message was received. Default implementation does nothing.
      Override in derived class.

      :param payload: Message payload (UTF-8 encoded text string or binary string). Can also be an empty string, when message contained no payload.
      :type payload: str
      :param binary: If True, payload is binary, otherwise text.
      :type binary: bool
      """
      if self.debug:
         log.msg("WebSocketProtocol.onMessage")


   def onPing(self, payload):
      """
      Callback when Ping was received. Default implementation responds
      with a Pong. Override in derived class.

      :param payload: Payload of Ping, when there was any. Can be arbitrary, up to 125 octets.
      :type payload: str
      """
      if self.debug:
         log.msg("WebSocketProtocol.onPing")
      self.sendPong(payload)


   def onPong(self, payload):
      """
      Callback when Pong was received. Default implementation does nothing.
      Override in derived class.

      :param payload: Payload of Pong, when there was any. Can be arbitrary, up to 125 octets.
      """
      if self.debug:
         log.msg("WebSocketProtocol.onPong")


   def onClose(self, code, reason):
      """
      Callback when Close was received. The default implementation answers by
      sending a normal Close when no Close was sent before. Otherwise it drops
      the connection. Override in derived class.

      :param code: None or close status code, if there was one (:class:`WebSocketProtocol`.CLOSE_STATUS_CODE_*).
      :type code: int
      :param reason: None or close reason (when present, a status code MUST have been also be present).
      :type reason: str
      """
      if self.debug:
         log.msg("WebSocketProtocol.onClose")
      if self.closeAlreadySent:
         self.transport.loseConnection()
      else:
         self.sendClose(code = WebSocketProtocol.CLOSE_STATUS_CODE_NORMAL)


   def __init__(self):
      self.state = WebSocketProtocol.STATE_CLOSED
      self.send_state = WebSocketProtocol.SEND_STATE_GROUND


   def getState(self):
      return self.state


   def failConnection(self):
      self.failedByMe = True
      self.state = WebSocketProtocol.STATE_CLOSED
      self.transport.loseConnection()


   def protocolViolation(self, reason):
      if self.debug:
         log.msg("Failing connection on protocol violation : %s" % reason)
      self.failConnection()


   def connectionMade(self):
      """
      This is called by Twisted framework when a new TCP connection has been established
      and handed over to a Protocol instance (an instance of this class).
      """
      self.debug = self.factory.debug
      self.transport.setTcpNoDelay(True)
      self.peer = self.transport.getPeer()
      self.peerstr = "%s:%d" % (self.peer.host, self.peer.port)
      self.state = WebSocketProtocol.STATE_CONNECTING
      self.send_state = WebSocketProtocol.SEND_STATE_GROUND
      self.data = ""
      self.closeAlreadySent = False
      self.failedByMe = False


   def connectionLost(self, reason):
      """
      This is called by Twisted framework when a TCP connection was lost.
      """
      self.state = WebSocketProtocol.STATE_CLOSED


   def logRxOctets(self, data):
      if self.debug:
         d = str(buffer(data))
         log.msg("RX Octets from %s : %s" % (self.peerstr, binascii.b2a_hex(d)))


   def logTxOctets(self, data, sync):
      if self.debug:
         d = str(buffer(data))
         log.msg("TX Octets to %s : %s" % (self.peerstr, binascii.b2a_hex(d)))


   def logRxFrame(self, fin, rsv, opcode, masked, payload_len, mask, payload):
      if self.debug:
         d = str(buffer(payload))
         if mask:
            mmask = binascii.b2a_hex(mask)
         else:
            mmask = str(mask)
         log.msg("RX Frame from %s : fin = %s, rsv = %s, opcode = %s, masked = %s, payload_len = %s, mask = %s, payload = %s" % (self.peerstr, str(fin), str(rsv), str(opcode), str(masked), str(payload_len), mmask, binascii.b2a_hex(d)))


   def logTxFrame(self, opcode, payload, fin, rsv, mask, payload_len, chopsize, sync):
      if self.debug:
         d = str(buffer(payload))
         if mask:
            mmask = binascii.b2a_hex(mask)
         else:
            mmask = str(mask)
         log.msg("TX Frame to %s : fin = %s, rsv = %s, opcode = %s, mask = %s, payload_len = %s, chopsize = %s, sync = %s, payload = %s" % (self.peerstr, str(fin), str(rsv), str(opcode), mmask, str(payload_len), str(chopsize), str(sync), binascii.b2a_hex(d)))


   def dataReceived(self, data):
      """
      This is called by Twisted framework upon receiving data on TCP connection.
      """
      self.logRxOctets(data)

      self.data += data
      self.consumeData()


   def consumeData(self):

      buffered_len = len(self.data)

      ## WebSocket is open (handshake was completed) or close was sent
      ##
      if self.state in [WebSocketProtocol.STATE_OPEN, WebSocketProtocol.STATE_CLOSING]:

         while self.processData():
            pass

      ## WebSocket needs handshake
      ##
      elif self.state == WebSocketProtocol.STATE_CONNECTING:

         ## the implementation of processHandshake() in derived
         ## class needs to perform client or server handshake
         ## from other party here ..
         ##
         self.processHandshake()

      ## we failed the connection .. don't process any more data!
      ##
      elif self.state == WebSocketProtocol.STATE_CLOSED:
         log.msg("received data in STATE_CLOSED")

      ## should not arrive here (invalid state)
      ##
      else:
         raise Exception("invalid state")


   def processHandshake(self):
      """
      Process WebSockets handshake.
      """
      raise Exception("must implement handshake (client or server) in derived class")


   def syncSocket(self):

      ## FIXME: find suitable replacement for this code, which appears to break
      ## sometimes ..
      ##
      ## From the web: "You should never call reactor.doSelect. This isn't portable across
      ## reactors, and it could easily break the reactor by re-entering it where it isn't
      ## expecting to be re-entered."
      ##
      try:
         reactor.doIteration(0)
         return True
      except:
         return False # socket has already gone away ..


   def registerProducer(self, producer, streaming):
      self.transport.registerProducer(producer, streaming)


   def sendData(self, raw, sync = False, chopsize = None):
      """
      Wrapper for self.transport.write which allows to give a chopsize.
      When asked to chop up writing to TCP stream, we write only chopsize octets
      and then give up control to select() in underlying reactor so that bytes
      get onto wire immediately. Note that this is different from and unrelated
      to WebSockets data message fragmentation. Note that this is also different
      from the TcpNoDelay option which can be set on the socket.
      """
      if chopsize and chopsize > 0:
         i = 0
         n = len(raw)
         done = False
         while not done:
            j = i + chopsize
            if j >= n:
               done = True
               j = n
            self.logTxOctets(raw[i:j], True)
            self.transport.write(raw[i:j])

            ## This is where the "magic" happens. We give up control to the
            ## Twisted reactor, which calls into the OS Select(), which will
            ## then send out outstanding data. I suspect this Twisted call is
            ## probably not "intended" to be called by Twisted users, but it is
            ## the only "way" I found to work to attain the intended result.
            ##
            self.syncSocket()

            i += chopsize
      else:
         self.logTxOctets(raw, sync)
         self.transport.write(raw)
         if sync:
            self.syncSocket()


   def processData(self):
      """
      After WebSockets handshake has been completed, this procedure will do all
      subsequent processing of incoming bytes.
      """
      buffered_len = len(self.data)

      ## outside a frame, that is we are awaiting data which starts a new frame
      ##
      if self.current_frame is None:

         #buffered_len = len(self.data)

         ## need minimum of 2 octets to for new frame
         ##
         if buffered_len >= 2:

            ## FIN, RSV, OPCODE
            ##
            b = ord(self.data[0])
            frame_fin = (b & 0x80) != 0
            frame_rsv = (b & 0x70) >> 4
            frame_opcode = b & 0x0f

            ## MASK, PAYLOAD LEN 1
            ##
            b = ord(self.data[1])
            frame_masked = (b & 0x80) != 0
            frame_payload_len1 = b & 0x7f

            ## MUST be 0 when no extension defining
            ## the semantics of RSV has been negotiated
            ##
            if frame_rsv != 0:
               self.protocolViolation("RSV != 0 and no extension negotiated")
               return False

            ## all client-to-server frames MUST be masked
            ##
            if self.isServer and not frame_masked:
               self.protocolViolation("unmasked client to server frame")
               return False

            ## check frame
            ##
            if frame_opcode > 7: # control frame (have MSB in opcode set)

               ## control frames MUST NOT be fragmented
               ##
               if not frame_fin:
                  self.protocolViolation("fragmented control frame")
                  return False

               ## control frames MUST have payload 125 octets or less
               ##
               if frame_payload_len1 > 125:
                  self.protocolViolation("control frame with payload length > 125 octets")
                  return False

               ## check for reserved control frame opcodes
               ##
               if frame_opcode not in [8, 9, 10]:
                  self.protocolViolation("control frame using reserved opcode %d" % frame_opcode)
                  return False

               ## close frame : if there is a body, the first two bytes of the body MUST be a 2-byte
               ## unsigned integer (in network byte order) representing a status code
               ##
               if frame_opcode == 8 and frame_payload_len1 == 1:
                  self.protocolViolation("received close control frame with payload len 1")
                  return False

            else: # data frame

               ## check for reserved data frame opcodes
               ##
               if frame_opcode not in [0, 1, 2]:
                  self.protocolViolation("data frame using reserved opcode %d" % frame_opcode)
                  return False

               ## check opcode vs message fragmentation state 1/2
               ##
               if not self.inside_message and frame_opcode == 0:
                  self.protocolViolation("received continuation data frame outside fragmented message")
                  return False

               ## check opcode vs message fragmentation state 2/2
               ##
               if self.inside_message and frame_opcode != 0:
                  self.protocolViolation("received non-continuation data frame while inside fragmented message")
                  return False

            ## compute complete header length
            ##
            if frame_masked:
               mask_len = 4
            else:
               mask_len = 0

            if frame_payload_len1 <  126:
               frame_header_len = 2 + mask_len
            elif frame_payload_len1 == 126:
               frame_header_len = 2 + 2 + mask_len
            elif frame_payload_len1 == 127:
               frame_header_len = 2 + 8 + mask_len
            else:
               raise Exception("logic error")

            ## only proceed when we have enough data buffered for complete
            ## frame header (which includes extended payload len + mask)
            ##
            if buffered_len >= frame_header_len:

               i = 2

               ## extract extended payload length
               ##
               if frame_payload_len1 == 126:
                  frame_payload_len = struct.unpack("!H", self.data[i:i+2])[0]
                  i += 2
               elif frame_payload_len1 == 127:
                  frame_payload_len = struct.unpack("!Q", self.data[i:i+8])[0]
                  if frame_payload_len > 0x7FFFFFFFFFFFFFFF: # 2**63
                     self.protocolViolation("invalid data frame length (>2^63)")
                     return False
                  i += 8
               else:
                  frame_payload_len = frame_payload_len1

               ## when payload is masked, extract frame mask
               ##
               frame_mask = None
               frame_mask_array = []
               if frame_masked:
                  frame_mask = self.data[i:i+4]
                  for j in range(0, 4):
                     frame_mask_array.append(ord(frame_mask[j]))
                  i += 4

               ## remember rest (payload of current frame after header and everything thereafter)
               ##
               self.data = self.data[i:]

               ## ok, got complete frame header
               ##
               self.current_frame = FrameHeader(frame_opcode, frame_fin, frame_rsv, frame_payload_len, frame_mask, frame_mask_array)

               ## process begin on new frame
               ##
               self.onFrameBegin()

               ## reprocess when frame has no payload or and buffered data left
               ##
               return frame_payload_len == 0 or len(self.data) > 0

            else:
               return False # need more data
         else:
            return False # need more data

      ## inside a started frame
      ##
      else:

         ## cut out rest of frame payload
         ##
         rest = self.current_frame.length - self.current_frame.ptr
         if buffered_len >= rest:
            payload = bytearray(self.data[:rest])
            length = rest
            self.data = self.data[rest:]
         else:
            payload = bytearray(self.data)
            length = buffered_len
            self.data = ""

         if length > 0:
            ## unmask payload
            ##
            if self.current_frame.mask:
               for k in xrange(0, length):
                  payload[k] ^= self.current_frame.mask_array[(k + self.current_frame.ptr) % 4]

            ## process frame data
            ##
            self.onFrameData(payload)

         ## advance payload pointer and fire frame end handler when frame payload is complete
         ##
         self.current_frame.ptr += length
         if self.current_frame.ptr == self.current_frame.length:
            self.onFrameEnd()

         ## reprocess when no error occurred and buffered data left
         ##
         return len(self.data) > 0


   def onFrameBegin(self):
      if self.current_frame.opcode > 7:
         self.control_frame_data = bytearray()
      else:
         if not self.inside_message:
            self.inside_message = True
            self.onMessageBegin(self.current_frame.opcode)
         self.onMessageFrameBegin(self.current_frame.length, self.current_frame.rsv)


   def onFrameData(self, payload):
      if self.current_frame.opcode > 7:
         self.control_frame_data.extend(payload)
      else:
         self.onMessageFrameData(payload)


   def onFrameEnd(self):
      if self.current_frame.opcode > 7:
         self.processControlFrame()
      else:
         self.onMessageFrameEnd()
         if self.current_frame.fin:
            self.onMessageEnd()
            self.inside_message = False
      self.current_frame = None


   def processControlFrame(self):
      payload = str(self.control_frame_data)
      self.control_frame_data = None

      self.logRxFrame(self.current_frame.fin, self.current_frame.rsv, self.current_frame.opcode, self.current_frame.mask is not None, self.current_frame.length, self.current_frame.mask, payload)

      ## CLOSE frame
      ##
      if self.current_frame.opcode == 8:

         code = None
         reason = None

         plen = len(payload)
         if plen > 0:

            ## If there is a body, the first two bytes of the body MUST be a 2-byte
            ## unsigned integer (in network byte order) representing a status code
            ##
            code = struct.unpack("!H", payload[0:2])[0]

            ## Following the 2-byte integer the body MAY contain UTF-8
            ## encoded data with value /reason/, the interpretation of which is not
            ## defined by this specification.
            ##
            if plen > 2:
               try:
                  reason = unicode(payload[2:], 'utf8')
               except UnicodeDecodeError:
                  self.protocolViolation("received non-UTF-8 payload as close frame reason")
                  return False

         self.onClose(code, reason)

      ## PING frame
      ##
      elif self.current_frame.opcode == 9:
         self.onPing(payload)

      ## PONG frame
      ##
      elif self.current_frame.opcode == 10:
         self.onPong(payload)

      else:
         raise Exception("logic error")

      return True


   def sendFrame(self, opcode, payload = "", fin = True, rsv = 0, mask = None, payload_len = None, chopsize = None, sync = False):
      """
      Send out frame. Normally only used internally via sendMessage(), sendPing(), sendPong() and sendClose().

      This method deliberately allows to send invalid frames (that is frames invalid
      per-se, or frames invalid because of protocol state). Other than in fuzzing servers,
      calling methods will ensure that no invalid frames are sent.

      In addition, this method supports explicit specification of payload length.
      When payload_len is given, it will always write that many octets to the stream.
      It'll wrap within payload, resending parts of that when more octets were requested
      The use case is again for fuzzing server which want to sent increasing amounts
      of payload data to peers without having to construct potentially large messges
      themselfes.
      """
      if payload_len:
         if payload_len < 1 or len(payload) < 1:
            raise Exception("cannot construct repeated payload with length %d from payload of length %d" % (payload_len, len(payload)))
         l = payload_len
         pl = ''.join([payload for k in range(payload_len / len(payload))]) + payload[:payload_len % len(payload)]
      else:
         l = len(payload)
         pl = payload

      ## first byte
      ##
      b0 = 0
      if fin:
         b0 |= (1 << 7)
      b0 |= (rsv % 8) << 4
      b0 |= opcode % 128

      ## second byte, payload len bytes and mask
      ##
      b1 = 0
      el = ""
      if mask or not self.isServer:
         b1 |= 1 << 7
         if mask:
            mv = struct.pack("!I", mask)
         else:
            mv = struct.pack("!I", random.getrandbits(32))

         frame_mask = []
         for j in range(0, 4):
            frame_mask.append(ord(mv[j]))

         ## mask frame payload
         ##
         pl_ba = bytearray(pl)
         for k in xrange(0, l):
            pl_ba[k] ^= frame_mask[k % 4]
         plm = str(pl_ba)

      else:
         mv = ""
         plm = pl

      if l <= 125:
         b1 |= l
      elif l <= 0xFFFF:
         b1 |= 126
         el = struct.pack("!H", l)
      elif l <= 0x7FFFFFFFFFFFFFFF:
         b1 |= 127
         el = struct.pack("!Q", l)
      else:
         raise Exception("invalid payload length")

      raw = ''.join([chr(b0), chr(b1), el, mv, plm])

      ## log frame TX
      ##
      if mv != "":
         mmv = binascii.b2a_hex(mv)
      else:
         mmv = None
      self.logTxFrame(opcode, payload, fin, rsv, mmv, payload_len, chopsize, sync)

      ## send frame octets
      ##
      self.sendData(raw, sync, chopsize)


   def sendPing(self, payload = None):
      """
      Send out Ping to peer. A peer is expected to Pong back the payload a soon
      as "practical". When more than 1 Ping is outstanding at a peer, the peer may
      elect to respond only to the last Ping.

      :param payload: An optional, arbitrary payload of length < 126 octets.
      :type payload: str
      """
      if payload:
         l = len(payload)
         if l > 125:
            raise Exception("invalid payload for PING (payload length must be <= 125, was %d)" % l)
         self.sendFrame(opcode = 9, payload = payload)
      else:
         self.sendFrame(opcode = 9)


   def sendPong(self, payload = None):
      """
      Send out Pong to peer. A Pong frame MAY be sent unsolicited.
      This serves as a unidirectional heartbeat. A response to an unsolicited pong is "not expected".

      :param payload: An optional, arbitrary payload of length < 126 octets.
      :type payload: str
      """
      if payload:
         l = len(payload)
         if l > 125:
            raise Exception("invalid payload for PONG (payload length must be <= 125, was %d)" % l)
         self.sendFrame(opcode = 10, payload = payload)
      else:
         self.sendFrame(opcode = 10)


   def sendClose(self, code = None, reason = None):
      """Send close to WebSockets peer.

      :param code: An optional close status code (:class:`WebSocketProtocol`.CLOSE_STATUS_CODE_*).
      :type code: int
      :param reason: An optional close reason (when present, a status code MUST also be present).
      :type reason: str
      """
      plen = 0
      payload = ""

      if code is not None:

         if (not (code >= 3000 and code <= 3999)) and \
            (not (code >= 4000 and code <= 4999)) and \
            code not in [WebSocketProtocol.CLOSE_STATUS_CODE_NORMAL,
                         WebSocketProtocol.CLOSE_STATUS_CODE_GOING_AWAY,
                         WebSocketProtocol.CLOSE_STATUS_CODE_PROTOCOL_ERROR,
                         WebSocketProtocol.CLOSE_STATUS_CODE_PAYLOAD_NOT_ACCEPTED,
                         WebSocketProtocol.CLOSE_STATUS_CODE_FRAME_TOO_LARGE,
                         WebSocketProtocol.CLOSE_STATUS_CODE_TEXT_FRAME_NOT_UTF8]:
            raise Exception("invalid status code %d for close frame" % code)

         payload = struct.pack("!H", code)
         plen = 2

         if reason is not None:
            reason = reason.encode("UTF-8")
            plen += len(reason)
         else:
            reason = ""

         if plen > 125:
            raise Exception("close frame payload larger than 125 octets")

         payload += reason

      else:
         if reason is not None and reason != "":
            raise Exception("status reason '%s' without status code in close frame" % reason)

      self.sendFrame(opcode = 8, payload = payload)
      self.closeAlreadySent = True


   def beginMessage(self, opcode = MESSAGE_TYPE_TEXT):
      """
      Begin sending new message.

      :param opcode: Message type, normally either WebSocketProtocol.MESSAGE_TYPE_TEXT (default) or WebSocketProtocol.MESSAGE_TYPE_BINARY.
      """
      ## check if sending state is valid for this method
      ##
      if self.send_state != WebSocketProtocol.SEND_STATE_GROUND:
         raise Exception("WebSocketProtocol.beginMessage invalid in current sending state")

      if opcode not in [1, 2]:
         raise Exception("use of reserved opcode %d" % opcode)

      ## move into "begin message" state and remember opcode for later (when sending first frame)
      ##
      self.send_state = WebSocketProtocol.SEND_STATE_MESSAGE_BEGIN
      self.send_message_opcode = opcode


   def beginMessageFrame(self, length, reserved = 0, mask = None):
      """
      Begin sending new message frame.

      :param length: Length of frame which is started. Must be >= 0 and <= 2^63.
      :type length: int
      :param reserved: Reserved bits for frame (an integer from 0 to 7). Note that reserved != 0 is only legal when an extension has been negoiated which defines semantics.
      :type reserved: int
      :param mask: Optional frame mask. When given, this is used. When None and the peer is a client, a mask will be internally generated. For servers None is default.
      :type mask: str
      """
      ## check if sending state is valid for this method
      ##
      if self.send_state not in [WebSocketProtocol.SEND_STATE_MESSAGE_BEGIN, WebSocketProtocol.SEND_STATE_INSIDE_MESSAGE]:
         raise Exception("WebSocketProtocol.beginMessageFrame invalid in current sending state")

      if type(length) is not int or length < 0 or length > 0x7FFFFFFFFFFFFFFF: # 2**63
         raise Exception("invalid value for message frame length")

      if type(reserved) is not int or reserved < 0 or reserved > 7:
         raise Exception("invalid value for reserved bits")

      self.send_message_frame_length = length
      self.send_message_frame_octets_sent = 0

      if mask:
         if type(mask) is not str:
            raise Exception("mask must be a (byte) string")
         if len(mask) != 4:
            raise Exception("mask must have length 4")
         self.send_message_frame_mask = mask
      elif not self.isServer:
         self.send_message_frame_mask = random.getrandbits(32)
      else:
         self.send_message_frame_mask = None

      ## first byte
      ##
      b0 = (reserved % 8) << 4 # FIN = false .. since with streaming, we don't know when message ends

      if self.send_state == WebSocketProtocol.SEND_STATE_MESSAGE_BEGIN:
         self.send_state = WebSocketProtocol.SEND_STATE_INSIDE_MESSAGE
         b0 |= self.send_message_opcode % 128
      else:
         pass # message continuation frame

      ## second byte, payload len bytes and mask
      ##
      b1 = 0
      el = ""
      if self.send_message_frame_mask:
         b1 |= 1 << 7
         mv = struct.pack("!I", self.send_message_frame_mask)
         self.send_message_frame_mask_array = []
         for j in range(0, 4):
            self.send_message_frame_mask_array.append(ord(mv[j]))
      else:
         mv = ""

      if length <= 125:
         b1 |= length
      elif length <= 0xFFFF:
         b1 |= 126
         el = struct.pack("!H", length)
      elif length <= 0x7FFFFFFFFFFFFFFF:
         b1 |= 127
         el = struct.pack("!Q", length)
      else:
         raise Exception("invalid payload length")

      ## write message frame header
      ##
      header = ''.join([chr(b0), chr(b1), el, mv])
      self.transport.write(header)

      ## now we are inside message frame ..
      ##
      self.send_state = WebSocketProtocol.SEND_STATE_INSIDE_MESSAGE_FRAME


   def sendMessageFrameData(self, payload):
      """
      Send out data when within message frame (message was begun, frame was begun).
      Note that the frame is automatically ended when enough data has been sent
      that is, there is no endMessageFrame, since you have begun the frame specifying
      the frame length, which implicitly defined the frame end. This is different from
      messages, which you begin and end, since a message can contain an unlimited number
      of frames.

      :param payload: Data to send.
      :returns: int -- When frame still incomplete, returns outstanding octets, when frame complete, returns <= 0, when < 0, the amount of unconsumed data in payload argument.
      """
      ## check if sending state is valid for this method
      ##
      if self.send_state != WebSocketProtocol.SEND_STATE_INSIDE_MESSAGE_FRAME:
         raise Exception("WebSocketProtocol.sendMessageFrameData invalid in current sending state")

      rl = len(payload)
      if self.send_message_frame_octets_sent + rl > self.send_message_frame_length:
         l = self.send_message_frame_length - self.send_message_frame_octets_sent
         rest = -(rl - l)
         pl_ba = bytearray(payload[:l])
      else:
         l = rl
         rest = self.send_message_frame_length - self.send_message_frame_octets_sent - l
         pl_ba = bytearray(payload)

      ## mask frame payload
      ##
      if self.send_message_frame_mask:
         w = self.send_message_frame_octets_sent % 4
         for k in xrange(0, l):
            pl_ba[k] ^= self.send_message_frame_mask_array[(w + k) % 4]
            ## WARNING. This is silly: if you do it instead like the uncommended line, the memory footprint
            ## will run away ...
#            pl_ba[k] ^= self.send_message_frame_mask_array[(self.send_message_frame_octets_sent + k) % 4]

      ## send frame payload
      ##
      self.transport.write(str(pl_ba))

      pl_ba = None

      #if sync:
      #   self.syncSocket()

      ## advance frame payload pointer and check if frame payload was completely sent
      ##
      self.send_message_frame_octets_sent += l

      ## if we are done with frame, move back into "inside message" state
      ##
      if self.send_message_frame_octets_sent >= self.send_message_frame_length:
         self.send_state = WebSocketProtocol.SEND_STATE_INSIDE_MESSAGE

      ## when =0 : frame was completed exactly
      ## when >0 : frame is still uncomplete and that much amount is still left to complete the frame
      ## when <0 : frame was completed and there was this much unconsumed data in payload argument
      ##
      return rest


   def endMessage(self):
      """
      End a previously begun message. No more frames may be sent (for that message). You have to
      begin a new message before sending again.
      """
      ## check if sending state is valid for this method
      ##
      if self.send_state != WebSocketProtocol.SEND_STATE_INSIDE_MESSAGE:
         raise Exception("WebSocketProtocol.endMessage invalid in current sending state [%d]" % self.send_state)
      self.sendFrame(opcode = 0, fin = True)
      self.send_state = WebSocketProtocol.SEND_STATE_GROUND


   def sendMessageFrame(self, payload, reserved = 0, mask = None, sync = False):
      """
      When a message has begun, send a complete message frame in one go.
      """
      self.beginMessageFrame(len(payload), reserved, mask)
      self.sendMessageFrameData(payload, sync)


   def sendMessage(self, payload, binary = False, payload_frag_size = None, sync = False):
      """
      Send out a message in one go.

      You can send text or binary message, and optionally
      specifiy a payload fragment size. When the latter is given, the payload will
      be split up into frames with payload <= the payload_frag_size given.
      """
      ## (initial) frame opcode
      ##
      if binary:
         opcode = 2
      else:
         opcode = 1

      ## send unfragmented
      ##
      if payload_frag_size is None or len(payload) <= payload_frag_size:
         self.sendFrame(opcode = opcode, payload = payload, sync = sync)

      ## send data message in fragments
      ##
      else:
         if payload_frag_size < 1:
            raise Exception("payload fragment size must be at least 1 (was %d)" % payload_frag_size)
         i = 0
         n = len(payload)
         done = False
         first = True
         while not done:
            j = i + payload_frag_size
            if j > n:
               done = True
               j = n
            if first:
               self.sendFrame(opcode = opcode, payload = payload[i:j], fin = done, sync = sync)
               first = False
            else:
               self.sendFrame(opcode = 0, payload = payload[i:j], fin = done, sync = sync)
            i += payload_frag_size


class HttpException():
   def __init__(self, code, reason):
      self.code = code
      self.reason = reason


class WebSocketServerProtocol(WebSocketProtocol):
   """
   A Twisted protocol for WebSockets servers.
   """

   def onConnect(self, host, path, params, origin, protocols):
      """
      Callback when new WebSocket client connection is established.
      Throw HttpException when you don't want to accept WebSocket connection.
      Return accepted protocol from list of protocols provided by client or None.

      Override in derived class.
      """
      return None

   def connectionMade(self):
      WebSocketProtocol.connectionMade(self)
      if self.debug:
         log.msg("WebSocketServiceConnection.connectionMade")
         log.msg("connection accepted from %s" % self.peerstr)
      self.http_request = None
      self.http_headers = {}
      self.isServer = True


   def connectionLost(self, reason):
      WebSocketProtocol.connectionLost(self, reason)
      if self.debug:
         log.msg("WebSocketServiceConnection.connectionLost")
         log.msg("connection from %s lost" % self.peerstr)


   def processHandshake(self):
      """
      Process WebSockets handshake.
      """
      ## only proceed when we have fully received the HTTP request line and all headers
      ##
      end_of_header = self.data.find("\x0d\x0a\x0d\x0a")
      if end_of_header >= 0:

         ## extract HTTP headers
         ##
         ## FIXME: properly handle headers split accross multiple lines
         ##
         raw = self.data[:end_of_header].splitlines()
         self.http_request = raw[0].strip()
         for h in raw[1:]:
            i = h.find(":")
            if i > 0:
               key = h[:i].strip()
               value = h[i+1:].strip()
               self.http_headers[key] = value

         ## remember rest (after HTTP headers, if any)
         ##
         self.data = self.data[end_of_header + 4:]

         ## self.http_request & self.http_headers are now set
         ## => validate WebSocket handshake
         ##

         if self.debug:
            log.msg("received request line in handshake : %s" % str(self.http_request))
            log.msg("received headers in handshake : %s" % str(self.http_headers))

         ## HTTP Request line : METHOD, VERSION
         ##
         rl = self.http_request.split(" ")
         if len(rl) != 3:
            return self.sendHttpBadRequest("bad HTTP request line '%s'" % self.http_request)
         if rl[0] != "GET":
            return self.sendHttpBadRequest("illegal HTTP method '%s'" % rl[0])
         vs = rl[2].split("/")
         if len(vs) != 2 or vs[0] != "HTTP" or vs[1] not in ["1.1"]:
            return self.sendHttpBadRequest("bad HTTP version '%s'" % rl[2])

         ## HTTP Request line : REQUEST-URI
         ##
         ## FIXME: checking
         ##
         self.http_request_uri = rl[1]
         (scheme, loc, path, params, query, fragment) = urlparse.urlparse(self.http_request_uri)
         if fragment != "":
            return self.sendHttpBadRequest("HTTP Request URI with fragment identifier")
         self.http_request_path = path
         self.http_request_params = urlparse.parse_qs(query)

         ## Host
         ##
         ## FIXME: checking
         ##
         if not self.http_headers.has_key("Host"):
            return self.sendHttpBadRequest("HTTP Host header missing")
         self.http_request_host = self.http_headers["Host"].strip()

         ## Upgrade
         ##
         if not self.http_headers.has_key("Upgrade"):
            return self.sendHttpBadRequest("HTTP Upgrade header missing")
         if self.http_headers["Upgrade"] != "websocket":
            return self.sendHttpBadRequest("HTTP Upgrade header different from 'websocket'")

         ## Connection
         ##
         if not self.http_headers.has_key("Connection"):
            return self.sendHttpBadRequest("HTTP Connection header missing")
         connectionUpgrade = False
         for c in self.http_headers["Connection"].split(","):
            if c.strip() == "Upgrade":
               connectionUpgrade = True
               break
         if not connectionUpgrade:
            return self.sendHttpBadRequest("HTTP Connection header does not include 'Upgrade' value")

         ## Sec-WebSocket-Version
         ##
         if not self.http_headers.has_key("Sec-WebSocket-Version"):
            return self.sendHttpBadRequest("HTTP Sec-WebSocket-Version header missing")
         try:
            version = int(self.http_headers["Sec-WebSocket-Version"])
            if version < 8:
               return self.sendHttpBadRequest("Sec-WebSocket-Version %d not supported (only >= 8)" % version)
            else:
               self.websocket_version = version
         except:
            return self.sendHttpBadRequest("could not parse HTTP Sec-WebSocket-Version header '%s'" % self.http_headers["Sec-WebSocket-Version"])

         ## Sec-WebSocket-Protocol
         ##
         ##
         if self.http_headers.has_key("Sec-WebSocket-Protocol"):
            protocols = self.http_headers["Sec-WebSocket-Protocol"].split(",")
            # check for duplicates in protocol header
            pp = {}
            for p in protocols:
               if pp.has_key(p):
                  return self.sendHttpBadRequest("duplicate protocol '%s' specified in HTTP Sec-WebSocket-Protocol header" % p)
               else:
                  pp[p] = 1
            # ok, no duplicates, save list in order the client sent it
            self.websocket_protocols = protocols
         else:
            self.websocket_protocols = []

         ## Sec-WebSocket-Origin
         ## http://tools.ietf.org/html/draft-ietf-websec-origin-02
         ##
         ## FIXME: checking
         ##
         if self.http_headers.has_key("Sec-WebSocket-Origin"):
            origin = self.http_headers["Sec-WebSocket-Origin"].strip()
            if origin == "null":
               self.websocket_origin = None
            else:
               self.websocket_origin = origin
         else:
            self.websocket_origin = None

         ## Sec-WebSocket-Extensions
         ##
         if self.http_headers.has_key("Sec-WebSocket-Extensions"):
            pass

         ## Sec-WebSocket-Key
         ## http://tools.ietf.org/html/rfc4648#section-4
         ##
         if not self.http_headers.has_key("Sec-WebSocket-Key"):
            return self.sendHttpBadRequest("HTTP Sec-WebSocket-Version header missing")
         key = self.http_headers["Sec-WebSocket-Key"].strip()
         if len(key) != 24: # 16 bytes => (ceil(128/24)*24)/6 == 24
            return self.sendHttpBadRequest("bad Sec-WebSocket-Key (length must be 24 ASCII chars) '%s'" % key)
         if key[-2:] != "==": # 24 - ceil(128/6) == 2
            return self.sendHttpBadRequest("bad Sec-WebSocket-Key (invalid base64 encoding) '%s'" % key)
         for c in key[:-2]:
            if c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/":
               return self.sendHttpBadRequest("bad character '%s' in Sec-WebSocket-Key (invalid base64 encoding) '%s'" (c, key))

         ## WebSocket handshake validated
         ## => produce response

         ## Now fire onConnect() on derived class, to give that class a chance to accept or deny
         ## the connection. onConnect() may throw, in which case the connection is denied, or it
         ## may return a protocol from the protocols provided by client or None.
         ##
         try:
            protocol = self.onConnect(self.http_request_host, self.http_request_path, self.http_request_params, self.websocket_origin, self.websocket_protocols)
            if protocol and not (protocol in self.websocket_protocols):
               raise Exception("protocol accepted must be from the list client sent or null")
         except HttpException, e:
            return self.sendHttpRequestFailure(e.code, e.reason)

         ## compute Sec-WebSocket-Accept
         ##
         sha1 = hashlib.sha1()
         sha1.update(key + WebSocketProtocol.WS_MAGIC)
         sec_websocket_accept = base64.b64encode(sha1.digest())

         ## send response to complete WebSocket handshake
         ##
         response  = "HTTP/1.1 101 Switching Protocols\x0d\x0a"
         response += "Upgrade: websocket\x0d\x0a"
         response += "Connection: Upgrade\x0d\x0a"
         response += "Sec-WebSocket-Accept: %s\x0d\x0a" % sec_websocket_accept
         if protocol:
            response += "Sec-WebSocket-Protocol: %s\x0d\x0a" % protocol
         response += "\x0d\x0a"

         if self.debug:
            log.msg("send handshake : %s" % response)
         self.sendData(response)

         ## move into OPEN state
         ##
         self.state = WebSocketProtocol.STATE_OPEN
         self.current_frame = None
         self.inside_message = False

         ## fire handler on derived class
         ##
         self.onOpen()

         ## process rest, if any
         ##
         if len(self.data) > 0:
            self.consumeData()


   def sendHttpBadRequest(self, reason):
      """
      When problems/errors happen during WebSockets handshake, send HTTP
      Bad Request Error and terminate.
      """
      self.sendHttpRequestFailure(400, reason)


   def sendHttpRequestFailure(self, code, reason):
      """
      Send out HTTP error.
      """
      response  = "HTTP/1.1 %d %s\x0d\x0a" % (code, reason)
      response += "\x0d\x0a"
      if self.debug:
         log.msg("send handshake failure : %s" % response)
      self.sendData(response)
      self.transport.loseConnection()


class WebSocketServerFactory(protocol.ServerFactory):
   """
   A Twisted factory for WebSockets server protocols.
   """

   protocol = WebSocketServerProtocol

   def __init__(self, debug = False):
      self.debug = debug

   def startFactory(self):
      pass

   def stopFactory(self):
      pass


class WebSocketClientProtocol(WebSocketProtocol):
   """
   Client protocol for WebSockets.
   """

   def __init__(self, debug = False):
      self.debug = debug


   def connectionMade(self):
      WebSocketProtocol.connectionMade(self)
      if self.debug:
         log.msg("connection to %s established" % self.peerstr)
      self.http_request = None
      self.http_headers = {}
      self.isServer = False
      self.startHandshake()


   def connectionLost(self, reason):
      WebSocketProtocol.connectionLost(self, reason)
      if self.debug:
         log.msg("connection to %s lost" % self.peerstr)


   def startHandshake(self):
      self.websocket_key = base64.b64encode(os.urandom(16))

      request  = "GET %s HTTP/1.1\x0d\x0a" % self.factory.path
      request += "Host: localhost:9000\x0d\x0a"
      request += "Upgrade: websocket\x0d\x0a"
      request += "Connection: Upgrade\x0d\x0a"
      request += "Sec-WebSocket-Key: %s\x0d\x0a" % self.websocket_key
      request += "Sec-WebSocket-Version: 8\x0d\x0a"
      request += "\x0d\x0a"

      if self.debug:
         log.msg(request)

      self.sendData(request)


   def processHandshake(self):
      """
      Process WebSockets handshake.
      """
      ## only proceed when we have fully received the HTTP request line and all headers
      ##
      end_of_header = self.data.find("\x0d\x0a\x0d\x0a")
      if end_of_header >= 0:

         ## extract HTTP headers
         ##
         ## FIXME: properly handle headers split accross multiple lines
         ##
         raw = self.data[:end_of_header].splitlines()
         self.http_request = raw[0].strip()
         for h in raw[1:]:
            i = h.find(":")
            if i > 0:
               key = h[:i].strip()
               value = h[i+1:].strip()
               self.http_headers[key] = value

         ## remember rest (after HTTP headers, if any)
         ##
         self.data = self.data[end_of_header + 4:]

         ## self.http_request & self.http_headers are now set
         ## => validate WebSocket handshake
         ##

         if self.debug:
            log.msg("received request line in handshake : %s" % str(self.http_request))
            log.msg("received headers in handshake : %s" % str(self.http_headers))

         ## Response Line
         ##
         if self.http_request != "HTTP/1.1 101 Switching Protocols":
            pass

         ## Upgrade
         ##
         if not self.http_headers.has_key("Upgrade"):
            return self.sendHttpBadRequest("HTTP Upgrade header missing")
         if self.http_headers["Upgrade"] != "websocket":
            return self.sendHttpBadRequest("HTTP Upgrade header different from 'websocket'")

         ## Connection
         ##
         if not self.http_headers.has_key("Connection"):
            return self.sendHttpBadRequest("HTTP Connection header missing")
         connectionUpgrade = False
         for c in self.http_headers["Connection"].split(","):
            if c.strip() == "Upgrade":
               connectionUpgrade = True
               break
         if not connectionUpgrade:
            return self.sendHttpBadRequest("HTTP Connection header does not include 'Upgrade' value")

         ## compute Sec-WebSocket-Accept
         ##
         if not self.http_headers.has_key("Sec-WebSocket-Accept"):
            return self.sendHttpBadRequest("HTTP Sec-WebSocket-Accept header missing")
         else:
            sha1 = hashlib.sha1()
            sha1.update(self.websocket_key + WebSocketProtocol.WS_MAGIC)
            sec_websocket_accept = base64.b64encode(sha1.digest())

            if self.http_headers["Sec-WebSocket-Accept"] != sec_websocket_accept:
               pass

         ## move into OPEN state
         ##
         self.state = WebSocketProtocol.STATE_OPEN
         self.current_frame = None
         self.inside_message = False

         ## fire handler on derived class
         ##
         self.onOpen()

         ## process rest, if any
         ##
         if len(self.data) > 0:
            self.consumeData()


class WebSocketClientFactory(protocol.ClientFactory):
   """
   Client factory for WebSockets.
   """

   protocol = WebSocketClientProtocol

   def __init__(self, debug = False):
      self.debug = debug
      self.path = "/"
      random.seed()

   def clientConnectionFailed(self, connector, reason):
      reactor.stop()

   def clientConnectionLost(self, connector, reason):
      reactor.stop()
