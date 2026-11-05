# Sesli Komut

Türkçe konuşmayı yazıya çevirip seçtiğin pencereye yazan küçük bir Windows aracı. Claude Code'u terminalde kullanırken sesli giriş olmadığı için yazıldı. Terminal, tarayıcı ya da not defteri fark etmez, metin yazılabilen her pencerede çalışır.

Konuşma bilgisayarının kendi ekran kartında, internete çıkmadan yazıya çevrilir ([faster-whisper](https://github.com/SYSTRAN/faster-whisper), `large-v3-turbo`). RTX 4050'de bir cümle yaklaşık 0.4 saniyede çevriliyor. Ekran kartı yoksa ya da model yüklenemezse Google'ın konuşma tanıma servisine geçer, o zaman internet gerekir.

![Sesli Komut damlası](docs/damla.png)

## Neler yapıyor

- Ekranın kenarına ya da köşesine yapışan, cam görünümlü küçük bir damla olarak duruyor. Ekranda neredeyse yer kaplamıyor.
- Dinlerken damlada sesine göre oynayan çizgiler çıkıyor. Sustuğunda (1.8 saniye) kayıt kendiliğinden bitiyor.
- Çevrilen metni hedef pencereye yapıştırıyor, istersen Enter'a da basıyor.
- Fare yaklaşınca damla ona doğru hafifçe uzuyor. Sürükleyince en yakın kenara ya da köşeye yapışıyor ve yerini hatırlıyor.
- Whisper'ın sessizlikte uydurduğu "İzlediğiniz için teşekkür ederim", "Altyazı M.K." gibi kalıpları siliyor.

## Kurulum

Windows 10/11 ve Python 3.10 ya da üstü gerekiyor.

```
pip install -r requirements.txt
pythonw sesli_komut.py
```

İlk açılışta Whisper modeli (~1.6 GB) indirilir, sonrasında internet gerekmez. NVIDIA ekran kartın yoksa son iki paketi (`nvidia-*`) kurmayabilirsin, araç Google tanımayla çalışır.

## Kullanım

| Ne | Nasıl |
|---|---|
| Konuşmaya başla / bitir | **F9** ya da damlaya tıkla |
| Ayar paneli | **Shift+F9** ya da damlaya sağ tıkla |
| Damlayı taşı | Sürükle, kenara ya da köşeye yapışır |
| Kapat | Ayar panelinde **Uygulamadan çık** |

Ayar panelinde metnin yazılacağı pencereyi seçiyorsun (açılışta VS Code seçili gelir) ve otomatik Enter'ı açıp kapatıyorsun.

VS Code'da metin en son tıkladığın panele gider. Terminale yazmak istiyorsan terminal panelinin seçili olması gerekir.

## Nasıl çalışıyor

- Mikrofon açılışta bir kez dinlenip ortamın gürültü eşiği ölçülüyor. Kayıt bu eşiğe göre başlıyor ve bitiyor.
- Metin panoya alınıp Ctrl+V ile yapıştırılıyor, Türkçe karakterler böylece bozulmadan geçiyor.
- Damla Tk ile değil, Pillow'la çizilip piksel başına saydamlıkla (`UpdateLayeredWindow`) ekrana basılıyor. Kenarları bu yüzden yumuşak ve içi yarı saydam.
- Tek dosya: `sesli_komut.py`.
