#pragma once
namespace ApiServer {
void begin(bool radioReady);
void poll();
void setRadioReady(bool ready);
}
