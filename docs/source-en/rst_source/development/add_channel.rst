Add a Channel or Protocol
==========================

Subclass ``ChannelModel`` to plug in custom wireless propagation models or external ray-tracing RPC services without changing robot code:

.. code-block:: python

   from comm_harness.communication import (
       ChannelDecision,
       ChannelModel,
       Envelope,
       InMemoryCommBus,
       LocalhostCommServer,
   )


   class WallAttenuationChannel(ChannelModel):
       name = "wall_attenuation"

       def __init__(self, min_sinr_db: float = 5.0):
           self.min_sinr_db = min_sinr_db
           self.position_resolver = None

       def evaluate(self, envelope: Envelope, recipient: str, *, now: float) -> ChannelDecision:
           sinr_db = 12.5
           if sinr_db < self.min_sinr_db:
               return ChannelDecision(deliver=False, drop_reason="low_sinr_wall_blockage")
           return ChannelDecision(deliver=True, delay_s=0.02)


   bus = InMemoryCommBus(episode_id="custom_rf", channel=WallAttenuationChannel())
   server = LocalhostCommServer(bus, host="127.0.0.1", port=9100)
   server.start_background()
