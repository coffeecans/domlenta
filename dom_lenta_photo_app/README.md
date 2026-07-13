# DIY Дом Лента — массовая AI-обработка фото товаров

Веб-приложение на Flask для пакетной обработки фотографий товаров интернет-магазина DIY Дом Лента. Один исходный файл считается одним SKU. Приложение удаляет фон товара через `rembg`, создаёт каталожное фото на выбранном фоне и отправляет это каталожное изображение в облачный официальный API Black Forest Labs FLUX.1 Kontext [pro] для генерации контекстной сцены.

## Возможности

- массовая загрузка изображений из папки через веб-интерфейс;
- обязательный ввод только двух параметров партии: цвет каталожного фона и описание контекстной сцены;
- AI-удаление исходного фона товара через `rembg` / IS-Net;
- генерация контекстной сцены через облачный API Black Forest Labs FLUX.1 Kontext [pro];
- локальные модели FLUX, Stable Diffusion, `torch`, `diffusers`, `transformers` и CUDA не используются;
- фиксированные имена результатов `<sku>_ai_1.png` и `<sku>_ai_2.png`;
- генерация отдельных папок по SKU;
- добавление `processing_report.txt` в корень ZIP;
- скачивание результата ZIP-архивом для последующей загрузки в PIM.

## Структура результата

Для каждого исходного файла создаётся отдельная папка SKU:

```text
902884_1/
├── 902884_1.png
├── 902884_1_ai_1.png
└── 902884_1_ai_2.png
```

- `*_ai_1.png` — товар с AI-удалением исходного фона на выбранном каталожном фоне;
- `*_ai_2.png` — контекстная сцена, скачанная из результата FLUX Kontext API;
- `processing_report.txt` — отчет по каждому SKU: готово или ошибка.

## Настройка Black Forest Labs

1. Создайте API-ключ Black Forest Labs.
2. Добавьте кредиты в аккаунт Black Forest Labs: каждая генерация `*_ai_2.png` может расходовать платные кредиты.
3. Установите переменную окружения на сервере:

```bash
BFL_API_KEY=your_black_forest_labs_api_key
```

Для локальной разработки можно скопировать `.env.example` в `.env`. Файл `.env` не коммитится.

Без `BFL_API_KEY` обработка не запускается: пользователь увидит сообщение «Не настроен ключ Black Forest Labs. Добавьте BFL_API_KEY в переменные окружения сервера».

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```

Откройте http://127.0.0.1:5000.

Для проверки production-команды локально можно запустить:

```bash
gunicorn app:app
```

## Деплой на Render.com

В корне репозитория уже есть файлы для Render, а код приложения лежит в `dom_lenta_photo_app/`:

- `dom_lenta_photo_app/requirements.txt` — зависимости Web Service, включая `gunicorn`, `rembg[cpu]`, `requests` и `python-dotenv`;
- `render.yaml` — Blueprint-конфигурация Web Service;
- `Procfile` — альтернативная команда запуска `web: gunicorn app:app`;
- `.python-version` и `PYTHON_VERSION` в `render.yaml` — фиксированная версия Python для повторяемой сборки;
- `runtime.txt` — дополнительная совместимость для Heroku-style окружений.

### Вариант 1: Blueprint через `render.yaml`

1. Запушьте текущую ветку в GitHub/GitLab-репозиторий.
2. В Render откройте **Dashboard → New → Blueprint**.
3. Подключите репозиторий с этим проектом.
4. Добавьте переменную окружения `BFL_API_KEY` в настройках сервиса.
5. Render прочитает `render.yaml`, установит зависимости командой `pip install --upgrade --no-cache-dir -r dom_lenta_photo_app/requirements.txt` и запустит приложение командой `gunicorn --chdir dom_lenta_photo_app app:app`.
6. После успешной сборки приложение будет доступно по URL вида `https://<service-name>.onrender.com`.

### Вариант 2: ручной Web Service

1. В Render откройте **Dashboard → New → Web Service**.
2. Подключите репозиторий.
3. Укажите параметры:
   - **Language**: `Python 3`;
   - **Build Command**: `pip install --upgrade --no-cache-dir -r dom_lenta_photo_app/requirements.txt`;
   - **Start Command**: `gunicorn --chdir dom_lenta_photo_app app:app`;
   - **Health Check Path**: `/healthz`.
4. В разделе **Environment** добавьте `SECRET_KEY` и `BFL_API_KEY`.
5. Нажмите **Create Web Service** и дождитесь окончания сборки.

> Важно: приложение хранит исходники и ZIP-архивы во временной файловой системе сервиса. Это подходит для сценария «обработал → скачал», но не для долгосрочного хранения. Для постоянного хранения нужно подключить Render Disk или внешний object storage.
